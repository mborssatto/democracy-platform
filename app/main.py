"""FastAPI app: API + serves the single-page frontend."""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, connectors, db, gates, llm, pipeline, pseudo

app = FastAPI(title="Democracy Platform — Prototype")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


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
    con.close()


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/state")
def state():
    con = db.connect()
    counts = {
        "items": con.execute("SELECT COUNT(*) c FROM content_items").fetchone()["c"],
        "opportunities": con.execute("SELECT COUNT(*) c FROM opportunities WHERE status='open'").fetchone()["c"],
        "posted": con.execute("SELECT COUNT(*) c FROM posted_actions").fetchone()["c"],
    }
    con.close()
    return {"ai_mode": llm.mode(), "counts": counts, "config": config.as_dict()}


# ------------------------------------------------------------------ ingestion
@app.post("/api/ingest/{source}")
def ingest(source: str):
    fetchers = {"tiktok": connectors.fetch_tiktok,
                "junkipedia": connectors.fetch_junkipedia,
                "tipline": connectors.fetch_tipline}
    if source not in fetchers:
        raise HTTPException(404, f"Unknown source '{source}'. Available: {', '.join(sorted(fetchers))}.")
    con = db.connect()
    new = 0
    for item in fetchers[source]():
        # Pseudonymize at the boundary: the real handle never enters content_items.
        pseudo_id = pseudo.pseudonymize(item["real_author_id"])
        pseudo.store_mapping(con, item["real_author_id"], pseudo_id)
        cur = con.execute(
            "INSERT OR IGNORE INTO content_items "
            "(platform, platform_item_id, author_pseudo, author_followers_band, text, text_en, "
            " url, posted_at, ingested_at, views, views_prev, likes, comments, shares) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item["platform"], item["platform_item_id"], pseudo_id, item["followers_band"],
             item["text"], item["text_en"], item["url"], item["posted_at"], db.now(),
             item["views"], item["views_prev"], item["likes"], item["comments"], item["shares"]),
        )
        new += cur.rowcount
    db.audit(con, "ingestion", f"ingest_{source}", {"new_items": new})
    con.commit(); con.close()
    return {"source": source, "new_items": new}


class TipIn(BaseModel):
    url: str = Field(min_length=4)
    text: str = Field(min_length=3)
    note: str | None = None


@app.post("/api/tipline")
def tipline_submit(tip: TipIn):
    """The tipline is a REAL feature (no external integration needed)."""
    con = db.connect()
    pseudo_id = pseudo.pseudonymize("tipline:" + tip.url)
    con.execute(
        "INSERT INTO content_items (platform, platform_item_id, author_pseudo, author_followers_band, "
        "text, url, posted_at, ingested_at, views, views_prev) "
        "VALUES ('tipline', ?, ?, 'unknown', ?, ?, ?, ?, 6000, 0)",
        (f"tip-{abs(hash(tip.url + tip.text)) % 10**8}", pseudo_id, tip.text, tip.url, db.now(), db.now()),
    )
    db.audit(con, "community", "tipline_submission", {"url": tip.url})
    con.commit(); con.close()
    return {"ok": True}


# ------------------------------------------------------------------ pipeline
@app.post("/api/pipeline/run")
def pipeline_run():
    con = db.connect()
    pending = con.execute("SELECT COUNT(*) c FROM content_items WHERE status='pending'").fetchone()["c"]
    log = pipeline.run_pipeline(con)
    con.close()
    log["pending_before"] = pending
    if pending == 0:
        log["note"] = "Nothing new to process — ingest data first (or submit a tip)."
    return log


# ------------------------------------------------------------------ radar
@app.get("/api/radar")
def radar():
    con = db.connect()
    narratives = []
    for n in con.execute("SELECT * FROM narratives").fetchall():
        rows = con.execute(
            """SELECT ci.*, e.output_json score_json FROM content_items ci
               JOIN enrichments ec ON ec.item_id=ci.id AND ec.stage='classify'
               LEFT JOIN enrichments e ON e.item_id=ci.id AND e.stage='score'
               WHERE json_extract(ec.output_json,'$.narrative')=?
               ORDER BY ci.views DESC""", (n["code"],)).fetchall()
        if not rows:
            continue
        total_views = sum(r["views"] for r in rows)
        prev_views = sum(r["views_prev"] for r in rows)
        narratives.append({
            "code": n["code"], "label_de": n["label_de"], "label_en": n["label_en"],
            "description_en": n["description_en"],
            "post_count": len(rows), "total_views": total_views,
            "growth_pct": round(100 * (total_views - prev_views) / max(1, prev_views)),
            "items": [dict(r) | {"score_json": r["score_json"]} for r in rows[:6]],
        })
    narratives.sort(key=lambda x: -x["growth_pct"])
    con.close()
    return narratives


# ------------------------------------------------------------------ opportunities
@app.get("/api/opportunities")
def opportunities():
    con = db.connect()
    rows = con.execute(
        """SELECT o.*, ci.text, ci.text_en, ci.url item_url, ci.views, ci.platform,
                  ci.author_followers_band,
                  cm.claim_id, cl.fact_checker, cl.verdict, cl.url fc_url, cl.claim_en
           FROM opportunities o
           JOIN content_items ci ON ci.id=o.item_id
           LEFT JOIN claim_matches cm ON cm.item_id=ci.id
           LEFT JOIN claims cl ON cl.id=cm.claim_id
           WHERE o.status IN ('open','claimed')
           ORDER BY o.score DESC""").fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.post("/api/opportunities/{opp_id}/claim")
def claim(opp_id: int):
    con = db.connect()
    row = con.execute("SELECT status FROM opportunities WHERE id=?", (opp_id,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Opportunity not found — it may have expired.")
    if row["status"] != "open":
        con.close()
        raise HTTPException(409, "This opportunity was already claimed by someone else.")
    con.execute("UPDATE opportunities SET status='claimed', claimed_by='demo-user', claimed_at=? "
                "WHERE id=? AND status='open'", (db.now(), opp_id))
    db.audit(con, "demo-user", "opportunity_claimed", {"opportunity": opp_id})
    con.commit(); con.close()
    return {"ok": True}


@app.post("/api/opportunities/{opp_id}/draft")
def create_draft(opp_id: int):
    con = db.connect()
    row = con.execute(
        """SELECT o.id, ci.text, cl.fact_checker, cl.verdict, cl.url, cl.claim_en
           FROM opportunities o JOIN content_items ci ON ci.id=o.item_id
           LEFT JOIN claim_matches cm ON cm.item_id=ci.id
           LEFT JOIN claims cl ON cl.id=cm.claim_id WHERE o.id=?""", (opp_id,)).fetchone()
    if not row:
        raise HTTPException(404, "opportunity not found")
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
def update_draft(draft_id: int, body: DraftUpdate):
    """Save a human revision and evaluate every gate. Returns full gate status."""
    con = db.connect()
    d = con.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not d:
        raise HTTPException(404, "draft not found")

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
        "SELECT COUNT(*) c FROM posted_actions WHERE date(posted_at)=date('now')").fetchone()["c"]
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
    db.audit(con, "demo-user", "gates_evaluated",
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
def mark_posted(draft_id: int, body: PostedIn):
    """The human posted it themselves; they report the URL back. The platform
    itself NEVER posts — there is deliberately no code path that could."""
    con = db.connect()
    d = con.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not d:
        raise HTTPException(404, "draft not found")
    if d["status"] != "post_ready":
        raise HTTPException(409, "draft has not passed all gates")
    con.execute("INSERT OR IGNORE INTO posted_actions (draft_id, posted_url, posted_at) VALUES (?,?,?)",
                (draft_id, body.posted_url, db.now()))
    con.execute("UPDATE drafts SET status='posted' WHERE id=?", (draft_id,))
    con.execute("UPDATE opportunities SET status='posted' "
                "WHERE id=(SELECT opportunity_id FROM drafts WHERE id=?)", (draft_id,))
    db.audit(con, "demo-user", "marked_posted", {"draft": draft_id, "url": body.posted_url})
    con.commit(); con.close()
    return {"ok": True}


# ------------------------------------------------------------------ studio
@app.post("/api/briefs/{narrative_code}")
def make_brief(narrative_code: str):
    con = db.connect()
    n = con.execute("SELECT * FROM narratives WHERE code=?", (narrative_code,)).fetchone()
    if not n:
        raise HTTPException(404, "unknown narrative")
    claims = [dict(c) for c in con.execute(
        "SELECT * FROM claims WHERE narrative_code=?", (narrative_code,)).fetchall()]
    examples = [r["text"] for r in con.execute(
        """SELECT ci.text FROM content_items ci
           JOIN enrichments e ON e.item_id=ci.id AND e.stage='classify'
           WHERE json_extract(e.output_json,'$.narrative')=? LIMIT 3""",
        (narrative_code,)).fetchall()]
    brief = llm.draft_brief(dict(n), claims, examples)
    db.audit(con, "demo-user", "brief_generated", {"narrative": narrative_code})
    con.commit(); con.close()
    return {"narrative": dict(n), "brief": brief, "factchecks": claims}


# ------------------------------------------------------------------ audit
@app.get("/api/audit")
def audit_view():
    con = db.connect()
    rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100").fetchall()
    con.close()
    return [dict(r) for r in rows]
