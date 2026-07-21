"""The enrichment pipeline: 6 small, versioned, re-runnable stages.

`enrich_item` runs all stages for ONE item; it is what the job queue executes
(app/queue.py + app/worker.py, SKIP LOCKED on Postgres). `run_pipeline` is the
synchronous "process everything now" path over all pending items — same code,
same order. Each stage writes one row to `enrichments`
(UNIQUE(item_id, stage) = idempotency: re-running never duplicates work).
"""
import json
import math

from . import config, connectors, db, llm

# Thresholds (amplification floor, score threshold, opportunity expiry) live in
# app/config.py — env-overridable, read at call time as config.NAME.

# Claim-match thresholds on the combined score (similarity + narrative prior).
# Embedding cosine and word overlap live on different scales, hence two numbers.
WORD_OVERLAP_THRESHOLD = 0.4
EMBED_MATCH_THRESHOLD = 0.35


def _store(con, item_id: int, stage: str, output: dict) -> None:
    con.execute(
        db.sql_upsert(
            "INTO enrichments (item_id, stage, output_json, model, created_at) VALUES (?,?,?,?,?)",
            conflict="item_id, stage", update_cols=("output_json", "model", "created_at")),
        (item_id, stage, json.dumps(output, ensure_ascii=False), llm.mode(), db.now()),
    )


def enrich_item(con, item, claims, item_vector=None, claim_vectors=None,
                embed_model=None) -> dict:
    """Run all 6 stages for one item. Returns a per-item stage log (0/1 counts,
    summable across items). Does NOT commit — the caller owns the transaction
    (run_pipeline below, or one job in app/worker.py).

    Embedding vectors may be precomputed by run_pipeline (one batch call for
    many items); when absent — the single-item worker path — they are fetched
    here after triage, so dropped items never cost an embedding call."""
    log = {"summarized": 0, "triage_kept": 0, "triage_dropped": 0, "classified": 0,
           "narrative_evaluated": 0, "claim_matched": 0, "opportunities": 0}

    # ---- Stage 0: summarize + topic -----------------------------------------
    # BEFORE the triage gate on purpose: the ingest cards show every analyzed
    # post, including triaged-out ones, and each needs its summary + topic.
    _store(con, item["id"], "summarize", llm.summarize(item["text"]))
    log["summarized"] += 1

    # ---- Stage 1: triage (+ translation) ------------------------------------
    verdict = llm.triage(item["text"])
    _store(con, item["id"], "triage", verdict)
    if not verdict["relevant"]:
        con.execute("UPDATE content_items SET status='triaged_out' WHERE id=?", (item["id"],))
        log["triage_dropped"] += 1
        return log
    if not item["text_en"]:
        con.execute("UPDATE content_items SET text_en=? WHERE id=?",
                    (llm.translate(item["text"]), item["id"]))
    log["triage_kept"] += 1

    # ---- Stage 2: classify into a narrative ---------------------------------
    codes = [r["code"] for r in con.execute("SELECT code FROM narratives").fetchall()]
    cls = llm.classify(item["text"], codes=codes)
    _store(con, item["id"], "classify", cls)
    log["classified"] += 1

    # ---- Stage 2b: narrative evaluation (auditable, versioned prompt) -------
    # Six categories incl. pro_democracy; the stored JSON carries the prompt
    # version + sha256 so every verdict is traceable to its exact prompt text.
    # NO fact-check verdicts are generated here (hard rule 3) — the
    # false_information category only records tracked-narrative matches.
    _store(con, item["id"], "narrative_eval", llm.narrative_eval(item["text"]))
    log["narrative_evaluated"] += 1

    # ---- Stage 3: embed ------------------------------------------------------
    # Real vectors when an embedding key is set; in mock mode word overlap
    # stands in and this stage records that honestly. Worker path (no
    # precomputed vectors): claim vectors come from the cache, the item text
    # is embedded on its own (post text only — hard rule 5).
    if claim_vectors is None:
        claim_vectors = ensure_claim_embeddings(con, claims)
        embed_model = embed_model or llm.embedding_model()
    if claim_vectors is not None and item_vector is None:
        item_vector = llm.embed_texts([item["text"]])[0]
    _store(con, item["id"], "embed",
           {"model": embed_model or "mock-standin",
            "dim": len(item_vector) if item_vector else 0})

    # ---- Stage 4: claim-match ------------------------------------------------
    # Embedding cosine when vectors exist, word-overlap fallback otherwise
    # (plan: pgvector on Postgres when the corpus outgrows in-memory cosine).
    best = _match_claim(item["text"], cls["narrative"], claims,
                        item_vector=item_vector, claim_vectors=claim_vectors)
    if best:
        claim, sim = best
        con.execute(
            db.sql_upsert(
                "INTO claim_matches (item_id, claim_id, similarity) VALUES (?,?,?)",
                conflict="item_id, claim_id", update_cols=("similarity",)),
            (item["id"], claim["id"], sim),
        )
        _store(con, item["id"], "claim_match",
               {"claim_id": claim["id"], "similarity": sim, "fact_checker": claim["fact_checker"]})
        log["claim_matched"] += 1

    # ---- Stage 5: score ------------------------------------------------------
    score, breakdown = _score(item, cls, bool(best))
    _store(con, item["id"], "score", {"score": score, "breakdown": breakdown})

    # ---- Stage 6: opportunity -----------------------------------------------
    if score >= config.SCORE_THRESHOLD:
        expiry_sql, expiry_param = db.sql_expiry(config.OPPORTUNITY_EXPIRY_HOURS)
        con.execute(
            db.sql_insert_ignore(
                "INTO opportunities "
                "(item_id, kind, score, score_breakdown, status, expires_at) "
                f"VALUES (?,?,?,?, 'open', {expiry_sql})"),
            (item["id"], "reply", score, json.dumps(breakdown), expiry_param),
        )
        log["opportunities"] += 1

    con.execute("UPDATE content_items SET status='enriched' WHERE id=?", (item["id"],))
    return log


def backfill_summaries(con) -> int:
    """Media items whose summary is missing OR predates the current shape
    (no "title" field yet) get re-summarized on the next run (idempotent —
    the LIKE probe makes an up-to-date row a no-op). Shared by run_pipeline
    and the /api/pipeline/run queue drain. Caller commits."""
    missing = con.execute(
        "SELECT ci.* FROM content_items ci "
        "JOIN media_extractions me ON me.item_id = ci.id "
        "WHERE ci.status != 'pending' AND NOT EXISTS "
        "(SELECT 1 FROM enrichments e WHERE e.item_id = ci.id AND e.stage = 'summarize' "
        " AND e.output_json LIKE '%\"title\"%')"
    ).fetchall()
    for item in missing:
        _store(con, item["id"], "summarize", llm.summarize(item["text"]))
    return len(missing)


def run_pipeline(con) -> dict:
    """Process every pending item through all stages, synchronously.
    Returns a stage-by-stage log."""
    log = {"summarized": 0, "triage_kept": 0, "triage_dropped": 0, "classified": 0,
           "narrative_evaluated": 0, "claim_matched": 0, "opportunities": 0,
           "mode": llm.mode()}

    items = con.execute("SELECT * FROM content_items WHERE status='pending'").fetchall()
    claims = con.execute("SELECT * FROM claims").fetchall()

    log["summarized"] += backfill_summaries(con)

    # Real embeddings when a key is configured (None in mock mode → word-overlap
    # fallback below). Claim vectors are cached; item texts are embedded in ONE
    # batch call up front (cost discipline: never one API call per item).
    embed_model = llm.embedding_model()
    claim_vectors = ensure_claim_embeddings(con, claims)
    item_vectors = {}
    if claim_vectors is not None and items:
        vecs = llm.embed_texts([it["text"] for it in items])  # post text only (hard rule 5)
        item_vectors = {it["id"]: v for it, v in zip(items, vecs)}

    for item in items:
        for key, n in enrich_item(con, item, claims,
                                  item_vector=item_vectors.get(item["id"]),
                                  claim_vectors=claim_vectors,
                                  embed_model=embed_model).items():
            log[key] += n

    db.audit(con, "pipeline", "run", log)
    con.commit()
    return log


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity in plain Python — honest at this corpus size (dozens
    of claims); pgvector is the documented scale path (docs/integrations/llm.md)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def ensure_claim_embeddings(con, claims, transport=None) -> dict | None:
    """Load cached claim vectors for the active model; embed + cache any missing
    ones in ONE batch call. Returns {claim_id: vector} or None in mock mode.

    `transport` is passed through to llm.embed_texts for tests (no network).
    """
    model = llm.embedding_model()
    if model is None:
        return None
    vectors, missing = {}, []
    for c in claims:
        row = con.execute(
            "SELECT vector_json FROM claim_embeddings WHERE claim_id=? AND model=?",
            (c["id"], model)).fetchone()
        if row:
            vectors[c["id"]] = json.loads(row["vector_json"])
        else:
            missing.append(c)
    if missing:
        # Claim text only — no author data exists on claims (hard rule 5 holds).
        vecs = llm.embed_texts([c["claim_de"] or c["claim_en"] or "" for c in missing],
                               transport=transport)
        for c, vec in zip(missing, vecs):
            vectors[c["id"]] = vec
            con.execute(
                db.sql_upsert(
                    "INTO claim_embeddings (claim_id, model, vector_json, created_at) VALUES (?,?,?,?)",
                    conflict="claim_id", update_cols=("model", "vector_json", "created_at")),
                (c["id"], model, json.dumps(vec), db.now()),
            )
        con.commit()
    return vectors


def _match_claim(text: str, narrative: str, claims,
                 item_vector=None, claim_vectors=None) -> tuple | None:
    """Match post text against the fact-check corpus. Narrative match is a strong
    prior (+0.4) on both paths. Returns (claim_row, similarity) or None.

    Embeddings path when vectors are provided (real mode): cosine similarity.
    Fallback (mock mode / no embedding key): word overlap — unchanged behavior."""
    if item_vector is not None and claim_vectors:
        best, best_sim = None, 0.0
        for c in claims:
            vec = claim_vectors.get(c["id"])
            if vec is None:
                continue
            sim = cosine(item_vector, vec) + (0.4 if c["narrative_code"] == narrative else 0.0)
            if sim > best_sim and sim >= EMBED_MATCH_THRESHOLD:
                best, best_sim = c, round(min(sim, 0.99), 2)
        return (best, best_sim) if best else None
    text_words = set(text.lower().split())
    best, best_sim = None, 0.0
    for c in claims:
        claim_words = set((c["claim_de"] or "").lower().split())
        overlap = len(text_words & claim_words) / max(1, len(claim_words))
        sim = overlap + (0.4 if c["narrative_code"] == narrative else 0.0)
        if sim > best_sim and sim >= WORD_OVERLAP_THRESHOLD:
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
            db.sql_insert_ignore(
                "INTO narratives (code, label_de, label_en, description_en, category) VALUES (?,?,?,?,?)"),
            (n["code"], n["label_de"], n["label_en"], n["description_en"],
             n.get("category", "false_information")),
        )
        # Databases seeded before the category column existed hold the default
        # ('false_information') — refresh it so re-seeding is truly idempotent.
        con.execute("UPDATE narratives SET category=? WHERE code=?",
                    (n.get("category", "false_information"), n["code"]))
    for c in connectors.load_factchecks():
        con.execute(
            db.sql_insert_ignore(
                "INTO claims (id, claim_de, claim_en, verdict, fact_checker, url, narrative_code, published_at, source) "
                "VALUES (?,?,?,?,?,?,?,?,'mock')"),
            (c["id"], c["claim_de"], c["claim_en"], c["verdict"],
             c["fact_checker"], c["url"], c["narrative_code"], c["published_at"]),
        )
    # The mock claims above carry explicit ids; on Postgres the identity
    # sequence must be moved past them (no-op on sqlite).
    db.resync_identity(con, "claims")
    con.commit()
