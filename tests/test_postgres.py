"""Postgres backend tests (M3.1).

Runs the same end-to-end pipeline invariants as the SQLite suite, but against
a real Postgres (the disposable docker db, or whatever DATABASE_URL points at).
The whole module skips cleanly when no server is reachable — the suite stays
green on machines without Postgres.
"""
import json

import pytest

from app import db, pipeline
from conftest import wipe_postgres
from test_pipeline import _ingest_all

psycopg = pytest.importorskip("psycopg")


def _postgres_reachable() -> bool:
    try:
        with psycopg.connect(db.database_url(), connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(),
    reason=f"no Postgres reachable at {db.database_url()} — "
           "start the disposable docker db to run these tests",
)


@pytest.fixture()
def pg_con(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    wipe_postgres()
    db.init_db()
    con = db.connect()
    pipeline.seed_reference_data(con)
    yield con
    con.close()
    wipe_postgres()  # leave no test data behind


def test_pipeline_end_to_end_on_postgres(pg_con):
    _ingest_all(pg_con)
    log = pipeline.run_pipeline(pg_con)
    assert log["triage_dropped"] >= 1          # the decoy is filtered out
    assert log["opportunities"] >= 4

    # Idempotent re-run: nothing pending, nothing duplicated.
    n1 = pg_con.execute("SELECT COUNT(*) c FROM opportunities").fetchone()["c"]
    pipeline.run_pipeline(pg_con)
    n2 = pg_con.execute("SELECT COUNT(*) c FROM opportunities").fetchone()["c"]
    assert n1 == n2


def test_canary_no_raw_author_ids_on_postgres(pg_con):
    """The pseudonymization canary must hold on BOTH backends."""
    _ingest_all(pg_con)
    pipeline.run_pipeline(pg_con)
    canaries = ["patriot_klartext_88", "wahrheit_ungefiltert", "deutschland_erwacht"]
    for table in ("content_items", "enrichments", "opportunities", "audit_log"):
        rows = pg_con.execute(f"SELECT * FROM {table}").fetchall()
        blob = json.dumps([dict(r) for r in rows], ensure_ascii=False)
        for canary in canaries:
            assert canary not in blob, f"raw author id leaked into {table}"


def test_lastrowid_adapter(pg_con):
    """The adapter's lastrowid (SELECT lastval()) matches the inserted row."""
    cur = pg_con.execute(
        "INSERT INTO drafts (opportunity_id, ai_seed, current_text, created_at) VALUES (?,?,?,?)",
        (1, "seed", "seed", db.now()))
    row = pg_con.execute("SELECT MAX(id) mx FROM drafts").fetchone()
    assert cur.lastrowid == row["mx"]
