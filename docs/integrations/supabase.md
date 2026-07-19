# Integration: Supabase (production database)

**Status in prototype: 🟢 IMPLEMENTED (DB_BACKEND=postgres)** — `app/db.py` is
backend-switchable. SQLite stays the zero-setup default for local dev; setting
two environment variables runs the whole platform on Postgres. Supabase is
hosted Postgres, so "swapping to Supabase" is just pasting their connection
string — no code changes.

## How to switch

```bash
# .env (or the environment)
DB_BACKEND=postgres
DATABASE_URL=postgres://postgres:dev@localhost:54329/democracy
```

- `DB_BACKEND` — `sqlite` (default) or `postgres`.
- `DATABASE_URL` — any Postgres connection string. For Supabase: project
  dashboard → Connect → connection string (choose the **Frankfurt (EU)**
  region when creating the project — data-protection requirement).

Tables are created automatically on startup from `migrations/001_init.sql`
(idempotent, `CREATE TABLE IF NOT EXISTS`). The same file works as a Supabase
CLI migration (`supabase migration new init`, paste it in).

## How it works (app/db.py)

- `connect()` returns a connection with the SAME usage on both backends:
  `con.execute(sql, params)` with `?` placeholders, rows indexable by column
  name, `con.commit()`, `con.close()`. On Postgres a ~40-line adapter
  (`PgConnection`) translates `?` → `%s`, uses psycopg's `dict_row`, and
  exposes `lastrowid` via `SELECT lastval()`.
- The few dialect differences are isolated in `db.sql_*` helpers:
  `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`, `INSERT OR REPLACE` →
  `ON CONFLICT ... DO UPDATE`, `json_extract(col,'$.k')` → `(col)::jsonb->>'k'`,
  `datetime('now', '+N hours')` → `now() + make_interval(...)`,
  `date(col)=date('now')` → `(col)::timestamp::date = current_date`.

## Testing against Postgres

`tests/test_postgres.py` runs the full pipeline (including the
pseudonymization canary) against `DATABASE_URL` and **skips cleanly when no
server is reachable**. A disposable local server:

```bash
docker run -d --name dp-postgres -p 54329:5432 \
  -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=democracy postgres:16-alpine
# the whole suite on Postgres:
DB_BACKEND=postgres python -m pytest tests/ -q
```

## Auth swap (production replaces the M3.2 stub)

The prototype's login (`app/auth.py`) is a dev-only stub: three seeded demo
users, the username doubling as the credential, and random bearer tokens in a
`sessions` table. In production, Supabase Auth takes over: users sign in with
email/SSO and Supabase issues a **JWT**. `get_user()` then verifies that JWT
with the project's JWT secret instead of looking tokens up in `sessions`
(which disappears, along with `/api/login`). Roles move to a `profiles` table
keyed by the auth user id (or `user_metadata`). As defense-in-depth, Row Level
Security re-enforces the same scoping in the database itself — e.g. responders
can only SELECT/UPDATE their own claimed opportunities — per plan Ch 8.2. The
route guards in `app/main.py` stay unchanged.

## What Supabase adds beyond the database

Auth (login for volunteers/staff), Row Level Security (per-tier access rules
from the full plan), Realtime (live Radar updates), Storage (Studio media),
pgvector (real embeddings for claim matching instead of the word-overlap
stand-in).

## Learn more
- Supabase in 100 seconds: https://www.youtube.com/watch?v=zBZgdTb-dns
- Row Level Security: https://supabase.com/docs/guides/database/postgres/row-level-security
