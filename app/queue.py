"""The job queue (M3.3): a plain `jobs` table drained by app/worker.py.

Semantics on both backends:
- Postgres: claim uses SELECT ... FOR UPDATE SKIP LOCKED, so any number of
  workers can pull jobs concurrently without double-claiming.
- SQLite: single-writer semantics make one atomic UPDATE ... RETURNING enough.

Transaction contract: NO function here commits. `claim_next` deliberately
leaves the transaction open — the caller processes the job and commits (or
rolls back) as one unit, so a crash mid-job returns the job to the queue.

Failure path: `fail` re-queues with exponential backoff (30s * 2^attempts)
until `max_attempts`, then parks the job in the 'dead' state (dead-letter) —
visible in the UI and re-queueable via POST /api/queue/dead/{id}/retry.
"""
import json
import time

from . import db

BACKOFF_BASE_SECONDS = 30


def _iso_in(seconds: float) -> str:
    """ISO timestamp `seconds` from now, same format as db.now()."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + seconds))


def enqueue(con, kind: str, payload: dict) -> int:
    """Insert a queued job, runnable immediately. Caller commits."""
    cur = con.execute(
        "INSERT INTO jobs (kind, payload, status, run_after, created_at, updated_at) "
        "VALUES (?,?,'queued',?,?,?)",
        (kind, json.dumps(payload), db.now(), db.now(), db.now()),
    )
    return cur.lastrowid


def claim_next(con):
    """Atomically claim the oldest runnable job: mark it 'running' and return
    it as a dict, or None when nothing is claimable. Does NOT commit — on
    Postgres the claimed row stays locked until the caller commits."""
    if db.backend() == "postgres":
        row = con.execute(
            "SELECT id FROM jobs WHERE status='queued' AND run_after <= ? "
            "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED",
            (db.now(),),
        ).fetchone()
        if row is None:
            return None
        job = con.execute(
            "UPDATE jobs SET status='running', updated_at=? WHERE id=? RETURNING *",
            (db.now(), row["id"]),
        ).fetchone()
        return dict(job)
    # SQLite: one atomic statement (RETURNING needs sqlite >= 3.35, which every
    # supported Python ships). The single-writer lock stands in for SKIP LOCKED.
    job = con.execute(
        "UPDATE jobs SET status='running', updated_at=? "
        "WHERE id = (SELECT id FROM jobs WHERE status='queued' AND run_after <= ? "
        "            ORDER BY id LIMIT 1) RETURNING *",
        (db.now(), db.now()),
    ).fetchone()
    return dict(job) if job else None


def complete(con, job_id: int) -> None:
    """Mark a job done. Caller commits."""
    con.execute("UPDATE jobs SET status='done', updated_at=? WHERE id=?", (db.now(), job_id))


def fail(con, job, error: str) -> None:
    """Record a failed attempt: re-queue with exponential backoff, or move to
    the dead-letter state after max_attempts. Caller commits."""
    attempts = job["attempts"] + 1
    if attempts >= job["max_attempts"]:
        con.execute(
            "UPDATE jobs SET status='dead', attempts=?, last_error=?, updated_at=? WHERE id=?",
            (attempts, error, db.now(), job["id"]),
        )
    else:
        backoff = BACKOFF_BASE_SECONDS * (2 ** attempts)
        con.execute(
            "UPDATE jobs SET status='queued', attempts=?, last_error=?, run_after=?, updated_at=? "
            "WHERE id=?",
            (attempts, error, _iso_in(backoff), db.now(), job["id"]),
        )


def queue_depth(con) -> dict:
    """Job counts by status, for the UI ("queued X · running Y · dead Z")."""
    depth = {"queued": 0, "running": 0, "done": 0, "dead": 0}
    for r in con.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall():
        if r["status"] in depth:
            depth[r["status"]] = r["c"]
    return depth
