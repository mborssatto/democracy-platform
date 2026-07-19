"""SQLite database layer.

Prototype note: the real plan uses Supabase (managed Postgres). SQLite keeps the
prototype zero-setup. The schema mirrors the plan's core tables, so swapping to
Postgres later is a connection-string + migration exercise, not a rewrite.
See docs/integrations/supabase.md for the exact swap steps.
"""
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "prototype.db"

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
    published_at TEXT
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
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db() -> None:
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    con.close()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def audit(con: sqlite3.Connection, actor: str, action: str, detail: dict) -> None:
    """Append-only audit log. Every gate event, vault access and override lands here."""
    con.execute(
        "INSERT INTO audit_log (actor, action, detail, created_at) VALUES (?,?,?,?)",
        (actor, action, json.dumps(detail, ensure_ascii=False), now()),
    )
