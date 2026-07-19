"""Fact-check sync tests (M2.1) — fully offline.

The Google Fact Check Tools API is simulated with httpx.MockTransport serving a
recorded-style response (mock_data/google_factcheck_response.json). We only ever
ingest published verdicts; nothing here generates one (hard rule #3).
"""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, factcheck, main, pipeline

FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "mock_data" / "google_factcheck_response.json")
    .read_text(encoding="utf-8")
)


@pytest.fixture()
def con(fresh_db, monkeypatch):
    monkeypatch.delenv("FACTCHECK_API_KEY", raising=False)
    con = db.connect()
    pipeline.seed_reference_data(con)
    yield con
    con.close()


def google_transport():
    """Every claims:search query answers with the recorded fixture — no network."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "factchecktools.googleapis.com"
        assert request.url.params["languageCode"] == "de"
        return httpx.Response(200, json=FIXTURE)
    return httpx.MockTransport(handler)


def test_sync_upserts_normalized_claims_with_snapshots(con):
    result = factcheck.sync_factchecks(con, transport=google_transport(), key="test-key")

    assert result["mode"] == "real"
    assert result["queried_keywords"] > 0
    assert result["upserted"] == 3  # 3 distinct fact-check URLs in the fixture

    rows = con.execute("SELECT * FROM claims WHERE source='google_factcheck'").fetchall()
    assert len(rows) == 3
    by_checker = {r["fact_checker"]: r for r in rows}
    assert set(by_checker) == {"CORRECTIV", "dpa-Faktencheck", "AFP Faktencheck"}

    # Verdicts normalized from German textualRating values
    assert by_checker["CORRECTIV"]["verdict"] == "false"          # "Falsch"
    assert by_checker["dpa-Faktencheck"]["verdict"] == "missing_context"  # "Fehlender Kontext"
    assert by_checker["AFP Faktencheck"]["verdict"] == "misleading"       # "Irreführend"

    # Full local snapshot stored (sources disappear; our copies don't)
    for r in rows:
        snap = json.loads(r["snapshot_json"])
        assert snap["claimReview"][0]["url"] == r["url"]
        assert r["claim_de"]          # claims[].text mapped
        assert r["claim_en"] is None  # no invented translation
        assert r["published_at"]      # claimReview[0].reviewDate mapped

    # Audit trail: the sync itself is a sensitive action
    audit = con.execute(
        "SELECT * FROM audit_log WHERE actor='sync' AND action='factcheck_sync'").fetchall()
    assert len(audit) == 1


def test_rerun_does_not_duplicate(con):
    factcheck.sync_factchecks(con, transport=google_transport(), key="test-key")
    before = con.execute("SELECT COUNT(*) c FROM claims").fetchone()["c"]
    ids_before = {r["id"] for r in con.execute(
        "SELECT id FROM claims WHERE source='google_factcheck'").fetchall()}

    factcheck.sync_factchecks(con, transport=google_transport(), key="test-key")
    after = con.execute("SELECT COUNT(*) c FROM claims").fetchone()["c"]
    ids_after = {r["id"] for r in con.execute(
        "SELECT id FROM claims WHERE source='google_factcheck'").fetchall()}

    assert after == before          # upsert keyed on url: no duplicates
    assert ids_after == ids_before  # rowids stable (claim_matches stay valid)


def test_mock_corpus_untouched_by_real_sync(con):
    factcheck.sync_factchecks(con, transport=google_transport(), key="test-key")
    mock_rows = con.execute("SELECT COUNT(*) c FROM claims WHERE source='mock'").fetchone()["c"]
    assert mock_rows == 5  # seeded fallback corpus stays


def test_normalize_verdict():
    assert factcheck.normalize_verdict("Falsch") == "false"
    assert factcheck.normalize_verdict("False") == "false"
    assert factcheck.normalize_verdict("Teilweise falsch") == "false"
    assert factcheck.normalize_verdict("Irreführend") == "misleading"
    assert factcheck.normalize_verdict("Misleading") == "misleading"
    assert factcheck.normalize_verdict("Fehlender Kontext") == "missing_context"
    assert factcheck.normalize_verdict("Missing context") == "missing_context"
    assert factcheck.normalize_verdict("Unbelegt") == "unbelegt"  # unknown: lowercased raw
    assert factcheck.normalize_verdict(None) == ""


# ------------------------------------------------------------------ endpoint
@pytest.fixture()
def client(fresh_db, monkeypatch):
    monkeypatch.delenv("FACTCHECK_API_KEY", raising=False)
    con = db.connect()
    pipeline.seed_reference_data(con)
    con.close()
    with TestClient(main.app, raise_server_exceptions=False) as c:
        from conftest import login_headers
        c.headers.update(login_headers(c, "staff"))  # sync endpoint is staff-only (M3.2)
        yield c


def test_endpoint_without_key_returns_mock_mode(client):
    r = client.post("/api/sync/factchecks")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock"
    assert body["upserted"] == 0
    assert "FACTCHECK_API_KEY" in body["note"]  # friendly explanation
