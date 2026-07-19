"""Database layer — SQLite by default, Postgres via DB_BACKEND=postgres.

SQLite keeps the prototype zero-setup (a file under data/). Setting
DB_BACKEND=postgres (+ DATABASE_URL) switches to Postgres via psycopg — any
Postgres works, including Supabase (which is hosted Postgres). Callers never
change: connect() always returns a connection used as
`con.execute(sql, params)` with '?' placeholders, rows indexable by column
name, `con.commit()`, `con.close()`. For Postgres that contract is provided
by a thin adapter (PgConnection below). See docs/integrations/supabase.md.

The few SQL constructs that differ between the dialects are isolated in the
sql_* helper functions at the bottom of this file.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "prototype.db"

DEFAULT_PG_URL = "postgres://postgres:dev@localhost:54329/democracy"
PG_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql"


def backend() -> str:
    """'sqlite' (default) or 'postgres'. Read from the environment at call
    time — not import time — so tests can monkeypatch it."""
    return os.environ.get("DB_BACKEND", "sqlite")


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_PG_URL)


SCHEMA = """
CREATE TABLE IF NOT EXISTS content_items (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,              -- tiktok | junkipedia | tipline
    platform_item_id TEXT NOT NULL,
    author_pseudo TEXT NOT NULL,         -- HMAC pseudonym, never the real handle
    author_followers_band TEXT,          -- e.g. "1k-10k" (banded, never exact)
    text TEXT NOT NULL,
    text_en TEXT,                        -- English translation (pipeline stage 1)
    lang TEXT DEFAULT 'de',
    url TEXT,
    posted_at TEXT,
    ingested_at TEXT,
    views INTEGER DEFAULT 0,
    views_prev INTEGER DEFAULT 0,        -- views ~24h earlier (for velocity)
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',       -- pending | triaged_out | enriched
    UNIQUE(platform, platform_item_id)
);

CREATE TABLE IF NOT EXISTS reid_vault (
    -- Simulates the separate, restricted vault. In production: separate schema,
    -- separate encryption key, access only via one audited function.
    author_pseudo TEXT PRIMARY KEY,
    real_id_encrypted TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE,
    label_de TEXT, label_en TEXT,
    description_en TEXT
);

CREATE TABLE IF NOT EXISTS enrichments (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL,
    stage TEXT NOT NULL,                 -- triage | classify | claim_match | score
    output_json TEXT NOT NULL,
    model TEXT,
    created_at TEXT,
    UNIQUE(item_id, stage)               -- idempotency: one result per stage
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    claim_de TEXT, claim_en TEXT,
    verdict TEXT,                        -- false | misleading | missing_context
    fact_checker TEXT,                   -- Correctiv / dpa / AFP (mock entries)
    url TEXT,
    narrative_code TEXT,
    published_at TEXT,
    snapshot_json TEXT,                  -- full local copy of the source record (APIs disappear; our copies don't)
    source TEXT                          -- mock | google_factcheck
);

CREATE TABLE IF NOT EXISTS claim_matches (
    item_id INTEGER, claim_id INTEGER, similarity REAL,
    PRIMARY KEY (item_id, claim_id)
);

CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY,
    item_id INTEGER UNIQUE,
    kind TEXT DEFAULT 'reply',
    score REAL,
    score_breakdown TEXT,                -- JSON, shown in the UI (explainability)
    status TEXT DEFAULT 'open',          -- open | claimed | posted | dismissed | expired
    claimed_by TEXT,
    claimed_at TEXT,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER,
    narrative_code TEXT,
    kind TEXT DEFAULT 'reply',           -- reply | brief
    strategy TEXT DEFAULT 'empathy',
    ai_seed TEXT NOT NULL,               -- immutable AI starting point
    current_text TEXT NOT NULL,
    status TEXT DEFAULT 'editing',       -- editing | post_ready | posted
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS draft_revisions (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    actor TEXT NOT NULL,                 -- ai | human
    edit_distance_pct REAL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS compliance_checks (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER NOT NULL,
    check_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    detail TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS posted_actions (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER UNIQUE,
    posted_url TEXT,
    posted_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    actor TEXT, action TEXT, detail TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    -- Auth stub (M3.2): three demo users, no passwords. Production: Supabase
    -- Auth owns identities; this becomes a profiles table keyed by auth uid.
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','staff','responder')),
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    -- Dev-only bearer tokens (24h). Production: Supabase JWTs, no table needed.
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT,
    expires_at TEXT
);
"""


# ------------------------------------------------------------- pg adapter
class PgCursor:
    """Mimics the bits of sqlite3.Cursor this codebase uses."""

    def __init__(self, cur, raw_con):
        self._cur = cur
        self._raw = raw_con

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def lastrowid(self) -> int:
        # Caveat: lastval() is session-wide, so this is only valid when read
        # immediately after an INSERT into an identity-column table — which is
        # exactly how sqlite3's lastrowid is used here. It would break with
        # triggers or when other inserts run in between.
        return self._raw.execute("SELECT lastval()").fetchone()["lastval"]


class PgConnection:
    """Thin adapter so a psycopg connection behaves like our sqlite3 usage:
    con.execute(sql, params) with '?' placeholders, dict-style rows,
    con.commit(), con.close()."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params=()):
        # '?' -> '%s' by plain replace: safe because no literal '?' appears
        # in any SQL string in this codebase (only as a placeholder).
        cur = self._raw.execute(sql.replace("?", "%s"), params or None)
        return PgCursor(cur, self._raw)

    def commit(self) -> None:
        self._raw.commit()

    def close(self) -> None:
        self._raw.close()


# ------------------------------------------------------------- connections
def connect():
    """Open a connection on the active backend. Same usage either way."""
    if backend() == "postgres":
        import psycopg  # imported lazily: sqlite mode needs no psycopg
        from psycopg.rows import dict_row

        return PgConnection(psycopg.connect(database_url(), row_factory=dict_row))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db() -> None:
    """Create all tables (idempotent on both backends)."""
    con = connect()
    if backend() == "postgres":
        con.execute(PG_MIGRATION.read_text())  # CREATE TABLE IF NOT EXISTS throughout
    else:
        con.executescript(SCHEMA)
    con.commit()
    con.close()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def audit(con, actor: str, action: str, detail: dict) -> None:
    """Append-only audit log. Every gate event, vault access and override lands here."""
    con.execute(
        "INSERT INTO audit_log (actor, action, detail, created_at) VALUES (?,?,?,?)",
        (actor, action, json.dumps(detail, ensure_ascii=False), now()),
    )


# ------------------------------------------------------------- sql dialect
# The handful of places where SQLite and Postgres SQL differ. Callers build
# their statements with these helpers; everything else is shared plain SQL.

def sql_insert_ignore(rest: str) -> str:
    """Idempotent insert: INSERT OR IGNORE (sqlite) vs ON CONFLICT DO NOTHING (pg)."""
    if backend() == "postgres":
        return f"INSERT {rest} ON CONFLICT DO NOTHING"
    return f"INSERT OR IGNORE {rest}"


def sql_upsert(rest: str, conflict: str, update_cols: tuple) -> str:
    """Overwrite-on-conflict insert: INSERT OR REPLACE (sqlite) vs
    ON CONFLICT ... DO UPDATE (pg, which also keeps the row id stable)."""
    if backend() == "postgres":
        sets = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
        return f"INSERT {rest} ON CONFLICT ({conflict}) DO UPDATE SET {sets}"
    return f"INSERT OR REPLACE {rest}"


def sql_json_extract(col: str, key: str) -> str:
    """One JSON field out of a TEXT column: json_extract vs ::jsonb ->>."""
    if backend() == "postgres":
        return f"({col})::jsonb ->> '{key}'"
    return f"json_extract({col}, '$.{key}')"


def sql_expiry(hours: int) -> tuple:
    """(SQL fragment, bind parameter) for 'now + N hours' (opportunity expiry)."""
    if backend() == "postgres":
        return "(now() + make_interval(hours => ?))::text", hours
    return "datetime('now', ?)", f"+{hours} hours"


def sql_is_today(col: str) -> str:
    """WHERE fragment: the date part of a stored timestamp equals today."""
    if backend() == "postgres":
        return f"({col})::timestamp::date = current_date"
    return f"date({col}) = date('now')"


def resync_identity(con, table: str) -> None:
    """Postgres only (no-op on sqlite): after seeding rows with explicit ids,
    move the identity sequence past MAX(id) so later auto-id inserts don't
    collide with the seeded rows."""
    if backend() != "postgres":
        return
    con.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"(SELECT COALESCE(MAX(id), 1) FROM {table}))"
    )
