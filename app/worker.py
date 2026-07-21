"""Background worker (M3.3): drains the job queue + optional periodic ingest.

A daemon thread started from app startup — but ONLY when RUN_WORKER=1
(`run.py` sets that default; tests leave it unset so they stay deterministic
and drain the queue synchronously instead).

Every poll cycle (WORKER_POLL_SECONDS, default 2):
  1. open a fresh connection, claim + process jobs until none are runnable;
  2. if SCHEDULE_INGEST_MINUTES > 0 and that long has passed, re-ingest all
     sources (mock-safe). The scheduler only ingests + enriches — there is
     deliberately NO code path here that posts anything (hard rule #1).

The thread never dies: every cycle is wrapped in a catch-all.
"""
import json
import os
import threading
import time

from . import db, outcomes, pipeline, queue


def poll_seconds() -> float:
    return float(os.environ.get("WORKER_POLL_SECONDS", "2"))


def schedule_ingest_minutes() -> float:
    return float(os.environ.get("SCHEDULE_INGEST_MINUTES", "0"))  # 0 = disabled


def process_job(con, job) -> dict | None:
    """Run one already-claimed job to completion, in the claim's transaction:
    enrich → complete → commit, or rollback → fail (backoff / dead-letter) →
    commit. Returns the per-item stage log, or None when nothing was enriched.
    Never raises on job errors — they land in the job row instead."""
    try:
        payload = json.loads(job["payload"])
        item_log = None
        if job["kind"] == "enrich_item":
            item_id = payload["item_id"]
            item = con.execute("SELECT * FROM content_items WHERE id=?", (item_id,)).fetchone()
            if item is None:
                raise ValueError(f"content item {item_id} not found")
            if item["status"] == "pending":  # idempotent: an already-enriched item is a no-op
                claims = con.execute("SELECT * FROM claims").fetchall()
                item_log = pipeline.enrich_item(con, item, claims)
        elif job["kind"] == "acquire_url":
            # M5.1: URL → Apify acquisition → media extraction → content item.
            from . import media_ingest  # lazy: keeps worker import-light

            # Long job (minutes of network: Apify run + model extraction) —
            # release the claim transaction first, or SQLite's single writer
            # lock starves every other write ("database is locked" on login).
            # Tradeoff: a crash mid-job leaves it 'running' (visible in the
            # queue UI) instead of auto-requeueing; run.py resets those at boot.
            con.commit()
            media_ingest.process_url(con, payload["url"])
        elif job["kind"] == "outcome_snapshot":
            # M4.2: re-check a thread we replied in (+1h/+24h/+72h) — mocked
            # fetch, idempotent write; see app/outcomes.py.
            outcomes.capture_snapshot(con, payload["posted_action_id"], payload["offset"])
        else:
            raise ValueError(f"unknown job kind '{job['kind']}'")
        queue.complete(con, job["id"])
        con.commit()
        return item_log
    except Exception as exc:
        try:  # leave the failed transaction (required on Postgres) — this also
            con.rollback()  # un-claims the job; fail() below re-marks it.
        except Exception:
            pass
        queue.fail(con, job, f"{type(exc).__name__}: {exc}")
        con.commit()
        return None


# ------------------------------------------------------------------ the thread
_started = False


def maybe_start() -> None:
    """Start the daemon worker thread once — only when RUN_WORKER=1."""
    global _started
    if _started or os.environ.get("RUN_WORKER") != "1":
        return
    _started = True
    threading.Thread(target=_loop, name="job-worker", daemon=True).start()


def _loop() -> None:
    last_ingest = time.monotonic()
    while True:
        try:
            _drain_available()
            last_ingest = _maybe_scheduled_ingest(last_ingest)
        except Exception:
            pass  # the worker must never die; job errors are recorded per-job
        time.sleep(poll_seconds())


def _drain_available() -> None:
    """One cycle: fresh connection, process runnable jobs until none are left
    (jobs failed in this cycle re-queue with a future run_after, so this ends)."""
    con = db.connect()
    try:
        while (job := queue.claim_next(con)) is not None:
            process_job(con, job)
        con.commit()  # close the last (empty) claim transaction
    finally:
        con.close()


def _maybe_scheduled_ingest(last_ingest: float) -> float:
    minutes = schedule_ingest_minutes()
    if minutes <= 0 or time.monotonic() - last_ingest < minutes * 60:
        return last_ingest
    from .main import SOURCES, ingest_source  # lazy: main imports us at startup

    con = db.connect()
    try:
        for source in SOURCES:
            ingest_source(con, source)  # pseudonymizes, inserts, enqueues enrich jobs
    finally:
        con.close()
    return time.monotonic()
