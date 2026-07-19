"""The enrichment pipeline: 6 small, versioned, re-runnable stages.

Prototype simplification: stages run synchronously when you press "Run pipeline"
(the full plan uses a Postgres job queue with SKIP LOCKED — same stages, same
order, just asynchronous). Each stage writes one row to `enrichments`
(UNIQUE(item_id, stage) = idempotency: re-running never duplicates work).
"""
import json

from . import config, connectors, db, llm

# Thresholds (amplification floor, score threshold, opportunity expiry) live in
# app/config.py — env-overridable, read at call time as config.NAME.


def _store(con, item_id: int, stage: str, output: dict) -> None:
    con.execute(
        "INSERT OR REPLACE INTO enrichments (item_id, stage, output_json, model, created_at) "
        "VALUES (?,?,?,?,?)",
        (item_id, stage, json.dumps(output, ensure_ascii=False), llm.mode(), db.now()),
    )


def run_pipeline(con) -> dict:
    """Process every pending item through all stages. Returns a stage-by-stage log."""
    log = {"triage_kept": 0, "triage_dropped": 0, "classified": 0,
           "claim_matched": 0, "opportunities": 0, "mode": llm.mode()}

    items = con.execute("SELECT * FROM content_items WHERE status='pending'").fetchall()
    claims = con.execute("SELECT * FROM claims").fetchall()

    for item in items:
        # ---- Stage 1: triage (+ translation) --------------------------------
        verdict = llm.triage(item["text"])
        _store(con, item["id"], "triage", verdict)
        if not verdict["relevant"]:
            con.execute("UPDATE content_items SET status='triaged_out' WHERE id=?", (item["id"],))
            log["triage_dropped"] += 1
            continue
        if not item["text_en"]:
            con.execute("UPDATE content_items SET text_en=? WHERE id=?",
                        (llm.translate(item["text"]), item["id"]))
        log["triage_kept"] += 1

        # ---- Stage 2: classify into a narrative -----------------------------
        cls = llm.classify(item["text"])
        _store(con, item["id"], "classify", cls)
        log["classified"] += 1

        # ---- Stage 3+4: embed & claim-match ---------------------------------
        # Prototype: word-overlap similarity stands in for embedding search
        # (plan: pgvector cosine over real embeddings — same idea, better math).
        best = _match_claim(item["text"], cls["narrative"], claims)
        if best:
            claim, sim = best
            con.execute(
                "INSERT OR REPLACE INTO claim_matches (item_id, claim_id, similarity) VALUES (?,?,?)",
                (item["id"], claim["id"], sim),
            )
            _store(con, item["id"], "claim_match",
                   {"claim_id": claim["id"], "similarity": sim, "fact_checker": claim["fact_checker"]})
            log["claim_matched"] += 1

        # ---- Stage 5: score --------------------------------------------------
        score, breakdown = _score(item, cls, bool(best))
        _store(con, item["id"], "score", {"score": score, "breakdown": breakdown})

        # ---- Stage 6: opportunity -------------------------------------------
        if score >= config.SCORE_THRESHOLD:
            con.execute(
                "INSERT OR IGNORE INTO opportunities "
                "(item_id, kind, score, score_breakdown, status, expires_at) "
                "VALUES (?,?,?,?, 'open', datetime('now', ?))",
                (item["id"], "reply", score, json.dumps(breakdown),
                 f"+{config.OPPORTUNITY_EXPIRY_HOURS} hours"),
            )
            log["opportunities"] += 1

        con.execute("UPDATE content_items SET status='enriched' WHERE id=?", (item["id"],))

    db.audit(con, "pipeline", "run", log)
    con.commit()
    return log


def _match_claim(text: str, narrative: str, claims) -> tuple | None:
    """Match post text against the fact-check corpus. Narrative match is a strong
    prior; word overlap breaks ties. Returns (claim_row, similarity) or None."""
    text_words = set(text.lower().split())
    best, best_sim = None, 0.0
    for c in claims:
        claim_words = set((c["claim_de"] or "").lower().split())
        overlap = len(text_words & claim_words) / max(1, len(claim_words))
        sim = overlap + (0.4 if c["narrative_code"] == narrative else 0.0)
        if sim > best_sim and sim >= 0.4:
            best, best_sim = c, round(min(sim, 0.99), 2)
    return (best, best_sim) if best else None


def _score(item, cls: dict, has_factcheck: bool) -> tuple[float, dict]:
    """Transparent weighted score. Every number is explainable in the UI."""
    # Hard floor: never surface low-reach content (amplification risk).
    if item["views"] < config.AMPLIFICATION_FLOOR_VIEWS:
        return 0.0, {"blocked": f"below amplification floor ({item['views']} < {config.AMPLIFICATION_FLOOR_VIEWS} views)"}

    velocity = (item["views"] - item["views_prev"]) / max(1, item["views_prev"])  # growth rate
    velocity_pts = min(40.0, round(velocity * 20, 1))
    reach_pts = {"<1k": 0, "1k-10k": 10, "10k-100k": 20, "100k+": 30}.get(item["author_followers_band"], 5)
    action_pts = 20 if has_factcheck else 5
    confidence_pts = round(10 * cls.get("confidence", 0.5), 1)

    total = round(velocity_pts + reach_pts + action_pts + confidence_pts, 1)
    return total, {
        "velocity": velocity_pts, "reach": reach_pts,
        "actionability (fact-check exists)": action_pts,
        "classification confidence": confidence_pts, "total": total,
    }


# ------------------------------------------------------------------ seeding
def seed_reference_data(con) -> None:
    """Load narratives + fact-check corpus (idempotent)."""
    for n in connectors.load_narratives():
        con.execute(
            "INSERT OR IGNORE INTO narratives (code, label_de, label_en, description_en) VALUES (?,?,?,?)",
            (n["code"], n["label_de"], n["label_en"], n["description_en"]),
        )
    for c in connectors.load_factchecks():
        con.execute(
            "INSERT OR IGNORE INTO claims (id, claim_de, claim_en, verdict, fact_checker, url, narrative_code, published_at, source) "
            "VALUES (?,?,?,?,?,?,?,?,'mock')",
            (c["id"], c["claim_de"], c["claim_en"], c["verdict"],
             c["fact_checker"], c["url"], c["narrative_code"], c["published_at"]),
        )
    con.commit()
