"""FastAPI app: API + serves the single-page frontend."""
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (auth, config, connectors, db, factcheck, gates, llm, media_ingest,
               outcomes, pipeline, pseudo, queue, worker)
from . import apify_tiktok

app = FastAPI(title="Democracy Platform — Prototype")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
app.include_router(auth.router)  # POST /api/login, /api/logout (dev auth stub)

# M4.1: the primary UI is the Nuxt SPA (frontend-nuxt/ → `npm run generate`).
# If the static build exists we serve it at / and mount its hashed assets at
# /_nuxt; with NO node build the repo still works — / falls back to the
# zero-build page (which is always available at /demo regardless).
NUXT_INDEX = Path(__file__).resolve().parent.parent / "frontend-nuxt" / ".output" / "public" / "index.html"
_nuxt_built = NUXT_INDEX.is_file()  # decided once at startup, like the mount below
if _nuxt_built:
    app.mount("/_nuxt", StaticFiles(directory=NUXT_INDEX.parent / "_nuxt"), name="nuxt_assets")

# Route guards (auth stub — see app/auth.py for the production Supabase swap):
#   public          GET /, /static, POST /api/login, GET /api/state
#   admin+staff     Radar, audit log, briefs, fact-check sync, ingestion, pipeline
#   any logged-in   tipline, Respond flow (responders scoped to their own claims)
STAFF = Depends(auth.require_role("admin", "staff"))
ANY_USER = Depends(auth.get_user)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort net: any unexpected error becomes a clean JSON 500 instead of
    an HTML traceback. Known error cases raise HTTPException with a real status."""
    return JSONResponse(
        status_code=500,
        content={"detail": f"Something went wrong on our side ({type(exc).__name__}). "
                           "Try again; if it persists, check the server log."},
    )


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    con = db.connect()
    pipeline.seed_reference_data(con)
    auth.seed_demo_users(con)
    # Jobs left 'running' by a crash/restart mid-job (long acquire_url jobs
    # commit their claim early — see worker.process_job) go back to 'queued'.
    con.execute("UPDATE jobs SET status='queued', updated_at=? WHERE status='running'",
                (db.now(),))
    con.commit()
    con.close()
    worker.maybe_start()  # background queue drain — only when RUN_WORKER=1


@app.get("/")
def index():
    """Nuxt build when present, zero-build page otherwise (see M4.1 note above)."""
    if _nuxt_built:
        return FileResponse(NUXT_INDEX)
    return FileResponse(FRONTEND / "index.html")


@app.get("/demo")
def demo():
    """The original zero-build Vue page — always served, needs no node toolchain."""
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/state")
def state(user: dict | None = Depends(auth.get_user_optional)):
    """Public, but echoes the logged-in user when a valid token is presented —
    the frontend uses this to restore a stored session on page load."""
    con = db.connect()
    counts = {
        "items": con.execute("SELECT COUNT(*) c FROM content_items").fetchone()["c"],
        "opportunities": con.execute("SELECT COUNT(*) c FROM opportunities WHERE status='open'").fetchone()["c"],
        "posted": con.execute("SELECT COUNT(*) c FROM posted_actions").fetchone()["c"],
    }
    con.close()
    return {"ai_mode": llm.mode(), "counts": counts, "config": config.as_dict(),
            "user": {"username": user["username"], "role": user["role"]} if user else None}


# ------------------------------------------------------------------ ingestion
SOURCES = {"tiktok": connectors.fetch_tiktok,
           "junkipedia": connectors.fetch_junkipedia,
           "tipline": connectors.fetch_tipline}


def ingest_source(con, source: str) -> dict:
    """Fetch one source, insert new items, and enqueue an 'enrich_item' job per
    NEW item. Shared by the ingest endpoint and the periodic scheduler."""
    new = 0
    for item in SOURCES[source]():
        # Pseudonymize-at-the-boundary + insert + enqueue live in media_ingest
        # so the URL ingestion path (M5.1) shares the exact same code.
        if media_ingest.insert_content_item(con, item) is not None:
            new += 1
    db.audit(con, "ingestion", f"ingest_{source}", {"new_items": new, "enqueued": new})
    con.commit()
    return {"source": source, "new_items": new, "enqueued": new}


class UrlsIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=50)


# NOTE: registered BEFORE /api/ingest/{source} — FastAPI matches in
# registration order, and the dynamic route would swallow "urls" as a source.
@app.post("/api/ingest/urls")
def ingest_urls(body: UrlsIn, user: dict = STAFF):
    """M5.1: submit TikTok post URLs for acquisition + analysis. Each valid URL
    becomes an 'acquire_url' job (Apify download → media extraction → pipeline);
    invalid rows come back marked instead of failing the batch."""
    if not apify_tiktok.enabled():
        raise HTTPException(
            503, "Apify acquisition is not configured (APIFY_TOKEN missing) — "
                 "URL ingestion is unavailable. See docs/integrations/apify.md.")
    con = db.connect()
    try:
        results, queued = [], 0
        for url in body.urls:
            url = url.strip()
            if media_ingest.is_tiktok_post_url(url):
                queue.enqueue(con, "acquire_url", {"url": url})
                results.append({"url": url, "status": "queued"})
                queued += 1
            else:
                results.append({"url": url, "status": "invalid",
                                "note": "not a TikTok post URL (…/@user/video/… or …/photo/…)"})
        db.audit(con, "ingestion", "ingest_urls",
                 {"submitted": len(body.urls), "queued": queued})
        con.commit()
        return {"results": results, "queued": queued}
    finally:
        con.close()


@app.post("/api/ingest/{source}")
def ingest(source: str, user: dict = STAFF):
    if source not in SOURCES:
        raise HTTPException(404, f"Unknown source '{source}'. Available: {', '.join(sorted(SOURCES))}.")
    con = db.connect()
    try:
        return ingest_source(con, source)
    finally:
        con.close()


class TipIn(BaseModel):
    url: str = Field(min_length=4)
    text: str = Field(min_length=3)
    note: str | None = None


@app.post("/api/tipline")
def tipline_submit(tip: TipIn, user: dict = ANY_USER):
    """The tipline is a REAL feature (no external integration needed)."""
    con = db.connect()
    pseudo_id = pseudo.pseudonymize("tipline:" + tip.url)
    cur = con.execute(
        "INSERT INTO content_items (platform, platform_item_id, author_pseudo, author_followers_band, "
        "text, url, posted_at, ingested_at, views, views_prev) "
        "VALUES ('tipline', ?, ?, 'unknown', ?, ?, ?, ?, 6000, 0)",
        (f"tip-{abs(hash(tip.url + tip.text)) % 10**8}", pseudo_id, tip.text, tip.url, db.now(), db.now()),
    )
    queue.enqueue(con, "enrich_item", {"item_id": cur.lastrowid})
    db.audit(con, "community", "tipline_submission", {"url": tip.url})
    con.commit(); con.close()
    return {"ok": True}


@app.post("/api/sync/factchecks")
def sync_factchecks(user: dict = STAFF):
    """Sync published fact-checks from the Google Fact Check Tools API into the
    `claims` table (real mode needs FACTCHECK_API_KEY; otherwise mock fallback).
    We only ever INGEST published verdicts — never generate them (hard rule #3)."""
    con = db.connect()
    result = factcheck.sync_factchecks(con)
    con.close()
    return result


# ------------------------------------------------------------------ pipeline
@app.post("/api/pipeline/run")
def pipeline_run(user: dict = STAFF):
    """Manual "process everything now": drains the job queue synchronously with
    the exact same claim/complete/fail semantics as the background worker, so
    retries and dead-lettering behave identically either way."""
    con = db.connect()
    try:
        pending = con.execute("SELECT COUNT(*) c FROM content_items WHERE status='pending'").fetchone()["c"]

        # Backfill: pending items without a live job (e.g. rows from before the
        # queue existed) get one, so "run" still means "process everything".
        covered = set()
        for r in con.execute("SELECT payload FROM jobs WHERE kind='enrich_item' "
                             "AND status IN ('queued','running')").fetchall():
            covered.add(json.loads(r["payload"]).get("item_id"))
        for r in con.execute("SELECT id FROM content_items WHERE status='pending'").fetchall():
            if r["id"] not in covered:
                queue.enqueue(con, "enrich_item", {"item_id": r["id"]})
        con.commit()

        log = {"triage_kept": 0, "triage_dropped": 0, "classified": 0,
               "narrative_evaluated": 0, "claim_matched": 0, "opportunities": 0,
               "mode": llm.mode()}
        drained = 0
        while (job := queue.claim_next(con)) is not None:
            item_log = worker.process_job(con, job)  # commits/fails per job
            for key, n in (item_log or {}).items():
                log[key] += n
            drained += 1
        log["jobs_processed"] = drained
        db.audit(con, "pipeline", "run", log)
        con.commit()
    finally:
        # Always release the connection — a leaked one holds locks on Postgres.
        con.close()
    log["pending_before"] = pending
    if pending == 0 and drained == 0:
        log["note"] = "Nothing new to process — ingest data first (or submit a tip)."
    return log


# ------------------------------------------------------------------ job queue
@app.get("/api/queue")
def queue_status(user: dict = STAFF):
    """Queue depth for the UI, plus the dead-letter view (jobs that failed
    max_attempts times and now wait for a human to inspect/retry)."""
    con = db.connect()
    depth = queue.queue_depth(con)
    dead = con.execute(
        "SELECT id, payload, attempts, last_error, updated_at FROM jobs "
        "WHERE status='dead' ORDER BY updated_at DESC, id DESC LIMIT 20").fetchall()
    con.close()
    return {"depth": depth, "dead": [dict(r) for r in dead]}


@app.post("/api/queue/dead/{job_id}/retry")
def retry_dead_job(job_id: int, user: dict = STAFF):
    """Put a dead-lettered job back in the queue with a fresh attempt budget."""
    con = db.connect()
    row = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Job not found.")
    if row["status"] != "dead":
        con.close()
        raise HTTPException(409, f"Job is '{row['status']}', not dead — only dead jobs can be retried.")
    con.execute("UPDATE jobs SET status='queued', attempts=0, run_after=?, updated_at=? WHERE id=?",
                (db.now(), db.now(), job_id))
    db.audit(con, "demo-user", "dead_job_retried", {"job": job_id})
    con.commit(); con.close()
    return {"ok": True}


@app.get("/api/analyzed")
def analyzed_posts(user: dict = STAFF):
    """Every post that went through media extraction (URL submissions), with
    its full analysis — shown regardless of narrative match or triage verdict,
    so the submitter always sees what the system read in the video/slides."""
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT ci.* FROM content_items ci "
            "JOIN media_extractions me ON me.item_id = ci.id "
            "ORDER BY ci.id DESC").fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["extraction"] = media_ingest.load_extraction(con, r["id"])
            for stage in ("triage", "classify", "narrative_eval"):
                e = con.execute(
                    "SELECT output_json FROM enrichments WHERE item_id=? AND stage=?",
                    (r["id"], stage)).fetchone()
                item[stage] = json.loads(e["output_json"]) if e else None
            out.append(item)
        return out
    finally:
        con.close()


# ------------------------------------------------------------------ radar
class NarrativeIn(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    label_de: str = Field(min_length=1)
    label_en: str = Field(min_length=1)
    description_en: str = Field(min_length=1)
    category: str


@app.post("/api/narratives")
def add_narrative(body: NarrativeIn, user: dict = STAFF):
    """Add a narrative to the Radar watchlist — false narratives, the harm
    categories (illegal content, fundamental rights, civic discourse,
    minors/minorities/public health), or pro_democracy entries creators can
    support. Category codes come from llm.EVAL_CATEGORIES."""
    if body.category not in llm.EVAL_CATEGORIES:
        raise HTTPException(
            422, f"Unknown category '{body.category}'. One of: {', '.join(llm.EVAL_CATEGORIES)}.")
    con = db.connect()
    try:
        cur = con.execute(
            db.sql_insert_ignore(
                "INTO narratives (code, label_de, label_en, description_en, category) "
                "VALUES (?,?,?,?,?)"),
            (body.code, body.label_de, body.label_en, body.description_en, body.category),
        )
        if not cur.rowcount:
            raise HTTPException(409, f"Narrative '{body.code}' already exists.")
        db.audit(con, "radar", "narrative_added",
                 {"code": body.code, "category": body.category, "by": user["username"]})
        con.commit()
        return {"ok": True, "code": body.code}
    finally:
        con.close()


@app.get("/api/radar")
def radar(user: dict = STAFF):
    con = db.connect()
    narratives = []
    for n in con.execute("SELECT * FROM narratives").fetchall():
        rows = con.execute(
            f"""SELECT ci.*, e.output_json score_json, ne.output_json eval_json
               FROM content_items ci
               JOIN enrichments ec ON ec.item_id=ci.id AND ec.stage='classify'
               LEFT JOIN enrichments e ON e.item_id=ci.id AND e.stage='score'
               LEFT JOIN enrichments ne ON ne.item_id=ci.id AND ne.stage='narrative_eval'
               WHERE {db.sql_json_extract('ec.output_json', 'narrative')}=?
               ORDER BY ci.views DESC""", (n["code"],)).fetchall()
        total_views = sum(r["views"] for r in rows)
        prev_views = sum(r["views_prev"] for r in rows)
        items = []
        for r in rows[:6]:
            item = dict(r) | {"score_json": r["score_json"]}
            item["narrative_eval"] = json.loads(r["eval_json"]) if r["eval_json"] else None
            item["extraction"] = media_ingest.load_extraction(con, r["id"])
            items.append(item)
        # Empty narratives stay visible: a just-added watchlist entry must
        # show up (with 0 posts) or adding it looks broken.
        narratives.append({
            "code": n["code"], "label_de": n["label_de"], "label_en": n["label_en"],
            "description_en": n["description_en"],
            "category": n["category"] or "false_information",
            "post_count": len(rows), "total_views": total_views,
            "growth_pct": round(100 * (total_views - prev_views) / max(1, prev_views)),
            "items": items,
        })
    narratives.sort(key=lambda x: (-x["post_count"], -x["growth_pct"]))
    con.close()
    return narratives


# ------------------------------------------------------------------ opportunities
@app.get("/api/opportunities")
def opportunities(user: dict = ANY_USER):
    """Staff/admin see every open+claimed opportunity; responders see open ones
    plus ONLY the ones they claimed themselves (data scoping)."""
    base = """SELECT o.*, ci.text, ci.text_en, ci.url item_url, ci.views, ci.platform,
                  ci.author_followers_band,
                  cm.claim_id, cl.fact_checker, cl.verdict, cl.url fc_url, cl.claim_en
           FROM opportunities o
           JOIN content_items ci ON ci.id=o.item_id
           LEFT JOIN claim_matches cm ON cm.item_id=ci.id
           LEFT JOIN claims cl ON cl.id=cm.claim_id
           WHERE {scope}
           ORDER BY o.score DESC"""
    con = db.connect()
    if user["role"] == "responder":
        rows = con.execute(
            base.format(scope="(o.status='open' OR (o.status='claimed' AND o.claimed_by=?))"),
            (user["username"],)).fetchall()
    else:
        rows = con.execute(base.format(scope="o.status IN ('open','claimed')")).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.post("/api/opportunities/{opp_id}/claim")
def claim(opp_id: int, user: dict = ANY_USER):
    con = db.connect()
    row = con.execute("SELECT status FROM opportunities WHERE id=?", (opp_id,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Opportunity not found — it may have expired.")
    if row["status"] != "open":
        con.close()
        raise HTTPException(409, "This opportunity was already claimed by someone else.")
    con.execute("UPDATE opportunities SET status='claimed', claimed_by=?, claimed_at=? "
                "WHERE id=? AND status='open'", (user["username"], db.now(), opp_id))
    db.audit(con, user["username"], "opportunity_claimed", {"opportunity": opp_id})
    con.commit(); con.close()
    return {"ok": True}


def _guard_responder_owns(con, user: dict, opp_id: int | None, draft_id: int | None = None) -> None:
    """Data scoping: a responder may only touch drafts/opportunities they
    claimed themselves. Staff/admin pass through. Raises an audited 403."""
    if user["role"] != "responder":
        return
    owner = con.execute("SELECT claimed_by FROM opportunities WHERE id=?", (opp_id,)).fetchone()
    if not owner or owner["claimed_by"] != user["username"]:
        auth.deny_scoped(con, user, "draft_scope",
                         {"opportunity": opp_id, "draft": draft_id})


@app.post("/api/opportunities/{opp_id}/draft")
def create_draft(opp_id: int, user: dict = ANY_USER):
    con = db.connect()
    row = con.execute(
        """SELECT o.id, ci.text, cl.fact_checker, cl.verdict, cl.url, cl.claim_en
           FROM opportunities o JOIN content_items ci ON ci.id=o.item_id
           LEFT JOIN claim_matches cm ON cm.item_id=ci.id
           LEFT JOIN claims cl ON cl.id=cm.claim_id WHERE o.id=?""", (opp_id,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "opportunity not found")
    _guard_responder_owns(con, user, opp_id)
    fc = {"fact_checker": row["fact_checker"], "verdict": row["verdict"], "url": row["url"]} \
        if row["fact_checker"] else None
    seed = llm.draft_reply(row["text"], fc)
    cur = con.execute(
        "INSERT INTO drafts (opportunity_id, kind, strategy, ai_seed, current_text, created_at) "
        "VALUES (?,?,?,?,?,?)", (opp_id, "reply", "empathy", seed, seed, db.now()))
    draft_id = cur.lastrowid
    con.execute("INSERT INTO draft_revisions (draft_id, text, actor, edit_distance_pct, created_at) "
                "VALUES (?,?,?,?,?)", (draft_id, seed, "ai", 0.0, db.now()))
    db.audit(con, "ai", "seed_drafted", {"draft": draft_id, "opportunity": opp_id})
    con.commit(); con.close()
    return {"draft_id": draft_id, "ai_seed": seed}


class DraftUpdate(BaseModel):
    text: str


@app.put("/api/drafts/{draft_id}")
def update_draft(draft_id: int, body: DraftUpdate, user: dict = ANY_USER):
    """Save a human revision and evaluate every gate. Returns full gate status."""
    con = db.connect()
    d = con.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not d:
        con.close()
        raise HTTPException(404, "draft not found")
    _guard_responder_owns(con, user, d["opportunity_id"], draft_id)

    dist = gates.token_edit_distance_pct(d["ai_seed"], body.text)
    con.execute("UPDATE drafts SET current_text=? WHERE id=?", (body.text, draft_id))
    con.execute("INSERT INTO draft_revisions (draft_id, text, actor, edit_distance_pct, created_at) "
                "VALUES (?,?,?,?,?)", (draft_id, body.text, "human", dist, db.now()))

    edit_ok, edit_msg, _ = gates.edit_gate(d["ai_seed"], body.text)
    neut_ok, neut_msg = gates.neutrality_check(body.text)
    recent = [r["current_text"] for r in con.execute(
        "SELECT current_text FROM drafts WHERE status='posted' AND id != ?", (draft_id,)).fetchall()]
    copy_ok, copy_msg = gates.copypasta_check(body.text, recent)
    dwell_ok, dwell_msg = gates.dwell_check(d["created_at"])
    posted_today = con.execute(
        f"SELECT COUNT(*) c FROM posted_actions WHERE {db.sql_is_today('posted_at')}").fetchone()["c"]
    rate_ok, rate_msg = gates.rate_cap_check(posted_today)

    checks = [
        ("edit_gate", edit_ok, edit_msg),
        ("neutrality", neut_ok, neut_msg),
        ("copypasta", copy_ok, copy_msg),
        ("dwell_time", dwell_ok, dwell_msg),
        ("rate_cap", rate_ok, rate_msg),
    ]
    con.execute("DELETE FROM compliance_checks WHERE draft_id=?", (draft_id,))
    for name, ok, msg in checks:
        con.execute("INSERT INTO compliance_checks (draft_id, check_name, passed, detail, created_at) "
                    "VALUES (?,?,?,?,?)", (draft_id, name, int(ok), msg, db.now()))
    all_ok = all(ok for _, ok, _ in checks)
    con.execute("UPDATE drafts SET status=? WHERE id=?",
                ("post_ready" if all_ok else "editing", draft_id))
    db.audit(con, user["username"], "gates_evaluated",
             {"draft": draft_id, "passed": all_ok, "edit_distance_pct": dist})
    con.commit(); con.close()
    return {
        "edit_distance_pct": dist,
        "post_ready": all_ok,
        "checks": [{"name": n, "passed": ok, "detail": msg} for n, ok, msg in checks],
    }


class PostedIn(BaseModel):
    posted_url: str


@app.post("/api/drafts/{draft_id}/mark-posted")
def mark_posted(draft_id: int, body: PostedIn, user: dict = ANY_USER):
    """The human posted it themselves; they report the URL back. The platform
    itself NEVER posts — there is deliberately no code path that could."""
    con = db.connect()
    d = con.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not d:
        con.close()
        raise HTTPException(404, "draft not found")
    _guard_responder_owns(con, user, d["opportunity_id"], draft_id)
    if d["status"] != "post_ready":
        con.close()
        raise HTTPException(409, "draft has not passed all gates")
    cur = con.execute(db.sql_insert_ignore("INTO posted_actions (draft_id, posted_url, posted_at) VALUES (?,?,?)"),
                      (draft_id, body.posted_url, db.now()))
    con.execute("UPDATE drafts SET status='posted' WHERE id=?", (draft_id,))
    con.execute("UPDATE opportunities SET status='posted' "
                "WHERE id=(SELECT opportunity_id FROM drafts WHERE id=?)", (draft_id,))
    # M4.2: schedule the +1h/+24h/+72h outcome snapshots — only for a NEWLY
    # recorded post (rowcount 0 = duplicate mark-posted; don't re-schedule).
    # lastrowid is unreliable after INSERT ... ON CONFLICT DO NOTHING on
    # Postgres, so fetch the id by the UNIQUE draft_id instead.
    if cur.rowcount:
        pa = con.execute("SELECT id FROM posted_actions WHERE draft_id=?", (draft_id,)).fetchone()
        outcomes.schedule_snapshots(con, pa["id"])
    db.audit(con, user["username"], "marked_posted", {"draft": draft_id, "url": body.posted_url})
    con.commit(); con.close()
    return {"ok": True}


# ------------------------------------------------------------------ outcomes (M4.2)
@app.get("/api/outcomes")
def outcomes_view(user: dict = STAFF):
    """Every posted reply with its thread snapshots and climate delta — the
    "is it working?" view. Plan Ch. 9: we measure the thread climate against a
    projected no-intervention baseline, not our own reply volume."""
    con = db.connect()
    posted = []
    for pa in con.execute("SELECT * FROM posted_actions ORDER BY id DESC").fetchall():
        snaps = con.execute(
            "SELECT * FROM outcome_snapshots WHERE posted_action_id=? ORDER BY id",
            (pa["id"],)).fetchall()
        posted.append(dict(pa) | {
            "snapshots": [dict(s) for s in snaps],
            "delta": outcomes.climate_delta(con, pa["id"]),
        })
    con.close()
    return {"posted": posted,
            "note": "Each posted reply is re-checked at +1h/+24h/+72h. The delta "
                    "compares thread climate against what the model projects had "
                    "nobody replied. Thread re-fetches are mocked in the prototype."}


@app.post("/api/outcomes/fast-forward")
def outcomes_fast_forward(user: dict = STAFF):
    """Demo "time travel": make every queued outcome_snapshot job runnable NOW,
    then drain the queue synchronously with the same claim/complete/fail loop
    as /api/pipeline/run. Production needs no such endpoint — the jobs'
    run_after already gates the real +1h/+24h/+72h timing; it just waits."""
    con = db.connect()
    try:
        cur = con.execute(
            "UPDATE jobs SET run_after=?, updated_at=? "
            "WHERE kind='outcome_snapshot' AND status='queued'",
            (db.now(), db.now()))
        advanced = cur.rowcount
        drained = 0
        while (job := queue.claim_next(con)) is not None:
            worker.process_job(con, job)  # commits/fails per job
            drained += 1
        db.audit(con, user["username"], "outcomes_fast_forward",
                 {"jobs_advanced": advanced, "jobs_processed": drained})
        con.commit()
    finally:
        con.close()
    return {"advanced": advanced, "processed": drained}


# ------------------------------------------------------------------ studio
@app.post("/api/briefs/{narrative_code}")
def make_brief(narrative_code: str, user: dict = STAFF):
    con = db.connect()
    n = con.execute("SELECT * FROM narratives WHERE code=?", (narrative_code,)).fetchone()
    if not n:
        con.close()
        raise HTTPException(404, "unknown narrative")
    claims = [dict(c) for c in con.execute(
        "SELECT * FROM claims WHERE narrative_code=?", (narrative_code,)).fetchall()]
    examples = [r["text"] for r in con.execute(
        f"""SELECT ci.text FROM content_items ci
           JOIN enrichments e ON e.item_id=ci.id AND e.stage='classify'
           WHERE {db.sql_json_extract('e.output_json', 'narrative')}=? LIMIT 3""",
        (narrative_code,)).fetchall()]
    brief = llm.draft_brief(dict(n), claims, examples)
    db.audit(con, user["username"], "brief_generated", {"narrative": narrative_code})
    con.commit(); con.close()
    return {"narrative": dict(n), "brief": brief, "factchecks": claims}


# ------------------------------------------------------------------ audit
@app.get("/api/audit")
def audit_view(user: dict = STAFF):
    con = db.connect()
    rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100").fetchall()
    con.close()
    return [dict(r) for r in rows]
