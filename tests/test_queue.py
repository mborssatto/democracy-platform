"""Job queue tests (M3.3): claim/complete/fail semantics, backoff, the
dead-letter state, the queue API, and the queue-backed pipeline drain.

The background worker thread must stay OFF here: tests never set RUN_WORKER,
and worker.maybe_start() is a no-op without it — the queue is drained
synchronously through the same code paths instead.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import db, main, queue, worker
from conftest import login_headers

FUTURE = "2999-01-01T00:00:00"


@pytest.fixture()
def con(fresh_db):
    c = db.connect()
    yield c
    c.close()


@pytest.fixture()
def client(fresh_db, monkeypatch):
    monkeypatch.delenv("RUN_WORKER", raising=False)  # keep the worker thread off
    with TestClient(main.app) as c:  # context manager runs startup (seeds claims)
        # Endpoints require auth since M3.2 — run this suite as staff.
        c.headers.update(login_headers(c, "staff"))
        yield c


def _job(con, job_id):
    return con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


# ------------------------------------------------------------------ primitives
def test_enqueue_then_claim_marks_running(con):
    job_id = queue.enqueue(con, "enrich_item", {"item_id": 1})
    con.commit()
    job = queue.claim_next(con)
    con.commit()
    assert job["id"] == job_id
    assert job["status"] == "running"
    assert json.loads(job["payload"]) == {"item_id": 1}
    assert _job(con, job_id)["status"] == "running"


def test_complete_marks_done(con):
    job_id = queue.enqueue(con, "enrich_item", {"item_id": 1})
    con.commit()
    job = queue.claim_next(con)
    queue.complete(con, job["id"])
    con.commit()
    assert _job(con, job_id)["status"] == "done"


def test_fail_requeues_with_backoff(con):
    job_id = queue.enqueue(con, "enrich_item", {"item_id": 1})
    con.commit()
    job = queue.claim_next(con)
    queue.fail(con, job, "boom")
    con.commit()
    row = _job(con, job_id)
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert row["last_error"] == "boom"
    assert row["run_after"] > db.now()   # backoff: not runnable yet
    assert queue.claim_next(con) is None  # ...so it cannot be claimed now
    con.commit()


def test_third_failure_goes_to_dead_letter(con):
    job_id = queue.enqueue(con, "enrich_item", {"item_id": 1})
    con.commit()
    for n in (1, 2, 3):
        queue.fail(con, _job(con, job_id), f"boom {n}")
        con.commit()
    row = _job(con, job_id)
    assert row["status"] == "dead"
    assert row["attempts"] == 3
    assert row["last_error"] == "boom 3"


def test_claim_skips_jobs_scheduled_for_the_future(con):
    queue.enqueue(con, "enrich_item", {"item_id": 1})
    con.execute("UPDATE jobs SET run_after=?", (FUTURE,))
    con.commit()
    assert queue.claim_next(con) is None
    con.commit()


def test_queue_depth_counts_by_status(con):
    ids = [queue.enqueue(con, "enrich_item", {"item_id": n}) for n in range(4)]
    con.commit()
    queue.claim_next(con)                       # ids[0] -> running
    job = queue.claim_next(con)                 # ids[1] -> done
    queue.complete(con, job["id"])
    for n in range(3):                          # ids[2] -> dead
        queue.fail(con, _job(con, ids[2]), "x")
    con.commit()                                # ids[3] stays queued
    assert queue.queue_depth(con) == {"queued": 1, "running": 1, "done": 1, "dead": 1}


@pytest.mark.skipif(db.backend() != "postgres",
                    reason="FOR UPDATE SKIP LOCKED is Postgres-only behavior")
def test_concurrent_claims_skip_locked(fresh_db):
    """Two connections claiming at once must get DIFFERENT jobs: the first
    holds its claim transaction open, the second must skip the locked row."""
    setup = db.connect()
    id1 = queue.enqueue(setup, "enrich_item", {"item_id": 1})
    id2 = queue.enqueue(setup, "enrich_item", {"item_id": 2})
    setup.commit(); setup.close()

    con1, con2 = db.connect(), db.connect()
    try:
        con2.execute("SET lock_timeout = '2s'")  # fail fast instead of blocking
        j1 = queue.claim_next(con1)              # transaction stays open — row locked
        j2 = queue.claim_next(con2)
        assert {j1["id"], j2["id"]} == {id1, id2}
        con1.commit(); con2.commit()
    finally:
        con1.close(); con2.close()


# ------------------------------------------------------------------ worker path
def test_process_job_dead_letters_bad_payloads(con):
    """A job pointing at a missing item fails through the real worker path and
    lands in the dead-letter state after max_attempts."""
    job_id = queue.enqueue(con, "enrich_item", {"item_id": 999999})
    con.commit()
    for _ in range(3):
        con.execute("UPDATE jobs SET run_after=? WHERE id=?", (db.now(), job_id))
        con.commit()                                  # skip the backoff wait
        assert worker.process_job(con, queue.claim_next(con)) is None
    row = _job(con, job_id)
    assert row["status"] == "dead"
    assert "not found" in row["last_error"]


# ------------------------------------------------------------------ API
def test_ingest_endpoint_enqueues_jobs(client):
    r = client.post("/api/ingest/tiktok").json()
    assert r["new_items"] > 0
    assert r["enqueued"] == r["new_items"]
    q = client.get("/api/queue").json()
    assert q["depth"]["queued"] == r["new_items"]
    # Re-ingesting the same fixture adds nothing and enqueues nothing.
    r2 = client.post("/api/ingest/tiktok").json()
    assert r2["new_items"] == 0 and r2["enqueued"] == 0
    assert client.get("/api/queue").json()["depth"]["queued"] == q["depth"]["queued"]


def test_pipeline_run_drains_queue_like_old_synchronous_run(client):
    """The queue-backed drain must reproduce the old synchronous invariants."""
    for src in ("tiktok", "junkipedia", "tipline"):
        client.post(f"/api/ingest/{src}")
    log = client.post("/api/pipeline/run").json()
    assert log["triage_dropped"] >= 1          # the cheesecake decoy is filtered out
    assert log["classified"] >= 8
    assert log["claim_matched"] >= 4
    assert log["opportunities"] >= 4
    assert log["jobs_processed"] >= 8

    q = client.get("/api/queue").json()
    assert q["depth"]["queued"] == 0 and q["depth"]["running"] == 0 and q["depth"]["dead"] == 0
    assert q["depth"]["done"] == log["jobs_processed"]

    # Canary intact: raw author handles never leak — including into `jobs`.
    con = db.connect()
    canaries = ["patriot_klartext_88", "wahrheit_ungefiltert", "deutschland_erwacht"]
    for table in ("content_items", "enrichments", "opportunities", "audit_log", "jobs"):
        blob = json.dumps([dict(r) for r in con.execute(f"SELECT * FROM {table}").fetchall()],
                          ensure_ascii=False)
        for canary in canaries:
            assert canary not in blob, f"raw author id leaked into {table}"
    con.close()


def test_pipeline_run_empty_queue_keeps_friendly_note(client):
    log = client.post("/api/pipeline/run").json()
    assert log["pending_before"] == 0
    assert "note" in log


def test_retry_endpoint_resets_dead_job_to_queued(client):
    con = db.connect()
    job_id = queue.enqueue(con, "enrich_item", {"item_id": 999999})
    con.commit()
    for n in (1, 2, 3):
        queue.fail(con, _job(con, job_id), f"boom {n}")
    con.commit()
    assert _job(con, job_id)["status"] == "dead"
    assert [d["id"] for d in client.get("/api/queue").json()["dead"]] == [job_id]

    assert client.post(f"/api/queue/dead/{job_id}/retry").status_code == 200
    row = _job(con, job_id)
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert row["run_after"] <= db.now()        # immediately claimable again
    assert client.get("/api/queue").json()["dead"] == []
    con.close()


def test_retry_endpoint_rejects_missing_and_live_jobs(client):
    assert client.post("/api/queue/dead/9999/retry").status_code == 404
    con = db.connect()
    job_id = queue.enqueue(con, "enrich_item", {"item_id": 1})
    con.commit(); con.close()
    assert client.post(f"/api/queue/dead/{job_id}/retry").status_code == 409
