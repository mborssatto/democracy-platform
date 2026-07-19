"""Google Fact Check Tools API sync (M2.1).

Pulls PUBLISHED fact-checks (ClaimReview standard) into the `claims` table so the
claim-matching stage can cite them. Hard rule #3 applies: we NEVER generate
verdicts — this module only ingests what professional fact-checkers published.

Real mode when FACTCHECK_API_KEY is in `.env` (free key — see
docs/integrations/factcheck.md). Without a key, the mock corpus seeded by
`pipeline.seed_reference_data` (mock_data/factchecks.json) stays as the fallback.

Every synced record keeps a full local snapshot (`snapshot_json`) — partners and
APIs disappear (Google already removed fact-check features from Search in 2025);
our copies must survive that.
"""
import json
import os

import httpx
from dotenv import load_dotenv

from . import db, pipeline
from .llm import NARRATIVE_KEYWORDS

load_dotenv()

API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"


def api_key() -> str | None:
    """Read at call time so tests can control the environment."""
    return os.environ.get("FACTCHECK_API_KEY")


def normalize_verdict(raw: str | None) -> str:
    """Map publishers' free-text ratings onto our small verdict vocabulary.
    Unknown ratings pass through lowercased — never invent a verdict."""
    r = (raw or "").strip().lower()
    if "kontext" in r or "context" in r:
        return "missing_context"
    if "falsch" in r or "false" in r:
        return "false"
    if "irreführend" in r or "misleading" in r:
        return "misleading"
    return r


def _upsert_claim(con, claim: dict, narrative_code: str) -> str | None:
    """Insert or update one API claim, keyed on the fact-check URL.
    Returns the url if a row was written, None if the record was unusable."""
    reviews = claim.get("claimReview") or []
    if not reviews:
        return None
    review = reviews[0]
    url = review.get("url")
    if not url:
        return None  # without a citable URL the record is useless to us
    row = (
        claim.get("text"),                              # claim_de
        normalize_verdict(review.get("textualRating")),  # verdict
        (review.get("publisher") or {}).get("name"),     # fact_checker
        narrative_code,
        review.get("reviewDate"),                        # published_at
        json.dumps(claim, ensure_ascii=False),           # snapshot_json
    )
    existing = con.execute("SELECT id FROM claims WHERE url=?", (url,)).fetchone()
    if existing:
        # SELECT-then-UPDATE (not INSERT OR REPLACE): keeps the rowid stable so
        # claim_matches rows pointing at this claim stay valid.
        con.execute(
            "UPDATE claims SET claim_de=?, verdict=?, fact_checker=?, narrative_code=?, "
            "published_at=?, snapshot_json=?, source='google_factcheck' WHERE id=?",
            row + (existing["id"],),
        )
    else:
        con.execute(
            "INSERT INTO claims (claim_de, claim_en, verdict, fact_checker, url, "
            "narrative_code, published_at, snapshot_json, source) "
            "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'google_factcheck')",
            (row[0], row[1], row[2], url, row[3], row[4], row[5]),
        )
    return url


def sync_factchecks(con, transport: httpx.BaseTransport | None = None,
                    key: str | None = None) -> dict:
    """Query the Google Fact Check Tools API once per narrative keyword and
    upsert results into `claims`. Re-running never duplicates (keyed on url).

    `transport` lets tests inject an httpx.MockTransport — no network needed.
    """
    key = key or api_key()
    if not key:
        # Fallback: make sure the mock corpus is present (idempotent) and say so.
        pipeline.seed_reference_data(con)
        result = {
            "mode": "mock", "queried_keywords": 0, "upserted": 0,
            "note": "No FACTCHECK_API_KEY set — using the built-in mock fact-check "
                    "corpus. Get a free key (docs/integrations/factcheck.md) to sync "
                    "real published fact-checks.",
        }
        db.audit(con, "sync", "factcheck_sync", {k: result[k] for k in ("mode", "queried_keywords", "upserted")})
        con.commit()
        return result

    queried, seen_urls = 0, set()
    with httpx.Client(transport=transport, timeout=30) as client:
        for narrative_code, keywords in NARRATIVE_KEYWORDS.items():
            for kw in keywords:
                r = client.get(API_URL, params={"query": kw, "languageCode": "de", "key": key})
                r.raise_for_status()
                queried += 1
                for claim in r.json().get("claims", []):
                    url = _upsert_claim(con, claim, narrative_code)
                    if url:
                        seen_urls.add(url)

    result = {"mode": "real", "queried_keywords": queried, "upserted": len(seen_urls)}
    db.audit(con, "sync", "factcheck_sync", result)
    con.commit()
    return result
