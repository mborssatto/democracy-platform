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

from dotenv import load_dotenv

# Bind the backend choice at process start. Several modules call load_dotenv()
# lazily inside request paths; without this pin, EDITING .env while the app is
# running could flip DB_BACKEND mid-process and split state across two
# databases (it did, 2026-07-21: sessions landed in one DB, jobs in another).
# load_dotenv never overrides an existing variable, so setting the default
# here freezes the choice for the process lifetime. Tests still monkeypatch
# DB_BACKEND explicitly, which always wins.
load_dotenv()
os.environ.setdefault("DB_BACKEND", "sqlite")

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
    description_en TEXT,
    -- evaluation category (llm.EVAL_CATEGORIES): false_information |
    -- illegal_content | fundamental_rights | civic_discourse |
    -- vulnerable_groups | pro_democracy
    category TEXT DEFAULT 'false_information'
);

CREATE TABLE IF NOT EXISTS enrichments (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL,
    stage TEXT NOT NULL,                 -- triage | classify | embed | claim_match | score
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

CREATE TABLE IF NOT EXISTS claim_embeddings (
    -- Cached claim vectors (M4.3). Only claims are cached — they are the
    -- stable corpus; item vectors live in memory for one pipeline run.
    -- vector_json = JSON array of floats; cosine happens in Python, which is
    -- honest at this corpus size (plan: pgvector column on Postgres at scale).
    claim_id INTEGER PRIMARY KEY,
    model TEXT,                          -- e.g. text-embedding-3-small
    vector_json TEXT,
    created_at TEXT
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
    created_by TEXT,                     -- who is writing it (queue flow)
    created_at TEXT
);

-- Personal response queue (2026-07-21): the Respond tab is a per-user list of
-- content the user chose to react to. No claiming/locking — by owner decision,
-- several users may draft against the same item.
CREATE TABLE IF NOT EXISTS response_queue (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    added_at TEXT,
    UNIQUE (username, item_id)
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

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                  -- enrich_item | outcome_snapshot
    payload TEXT NOT NULL,               -- JSON, e.g. {"item_id": 7}
    status TEXT DEFAULT 'queued',        -- queued | running | done | dead
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    run_after TEXT,                      -- ISO; claimable only when <= now (backoff)
    last_error TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS media_extractions (
    -- Media content extraction results (docs/integrations/media-extraction.md):
    -- the structured channels behind the item's merged_text. One per item.
    -- NO author fields here — the pseudonymization canary applies to this
    -- table too; merged_text is content, the author is only in content_items
    -- as a pseudonym.
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL UNIQUE,
    extractor TEXT NOT NULL,             -- mock | openrouter | local
    has_speech INTEGER NOT NULL,         -- VAD/model verdict, 0/1
    speech_json TEXT NOT NULL,           -- [{text,start_s,end_s}]
    on_screen_json TEXT NOT NULL,        -- [{text,source:{...}}]
    caption TEXT,
    merged_text TEXT NOT NULL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS outcome_snapshots (
    -- M4.2: thread re-checks at +1h/+24h/+72h after a reply was posted, plus
    -- the projected no-intervention baseline per offset (plan Ch. 9: measure
    -- thread climate, not reply volume). Captured by 'outcome_snapshot' jobs;
    -- the fetch is MOCKED in the prototype (no real thread API). Deliberately
    -- NO author fields — the pseudonymization canary applies here too.
    id INTEGER PRIMARY KEY,
    posted_action_id INTEGER NOT NULL,
    offset_label TEXT NOT NULL,          -- '+1h' | '+24h' | '+72h'
    captured_at TEXT,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    sentiment_score REAL,                -- -1..1 thread climate
    reply_count INTEGER,
    is_baseline INTEGER DEFAULT 0,       -- 1 = projected non-intervened baseline
    UNIQUE(posted_action_id, offset_label, is_baseline)
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

    def rollback(self) -> None:
        self._raw.rollback()

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
    # check_same_thread=False: the background worker (app/worker.py) opens its
    # connections on its own thread; each connection is still used by one
    # thread at a time, which is what sqlite actually requires.
    # timeout=30: tolerate another thread briefly holding the single writer
    # lock (default 5s surfaced as "database is locked" under the worker).
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # With the background worker on, HTTP writes and worker writes can collide;
    # wait briefly for the lock instead of failing with "database is locked".
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def init_db() -> None:
    """Create all tables (idempotent on both backends)."""
    con = connect()
    if backend() == "postgres":
        con.execute(PG_MIGRATION.read_text())  # CREATE TABLE IF NOT EXISTS throughout
    else:
        con.executescript(SCHEMA)
        # Column added 2026-07-20 (narrative categories): CREATE TABLE IF NOT
        # EXISTS never alters an existing table, so patch older SQLite files
        # here. SQLite has no ADD COLUMN IF NOT EXISTS → try/except.
        try:
            con.execute("ALTER TABLE narratives ADD COLUMN category TEXT DEFAULT 'false_information'")
        except Exception:
            pass  # column already there
        # Column added 2026-07-21 (response queue: per-user drafts, no claiming)
        try:
            con.execute("ALTER TABLE drafts ADD COLUMN created_by TEXT")
        except Exception:
            pass  # column already there
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
