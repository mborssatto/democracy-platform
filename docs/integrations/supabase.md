# Integration: Supabase (production database)

**Status in prototype: 🟡 SUBSTITUTED** — SQLite stands in for Supabase Postgres
so the prototype needs zero accounts. The schema in `app/db.py` mirrors the plan's
core tables on purpose.

## Step-by-step: swapping to Supabase

1. Create a project at https://supabase.com/ (choose the **Frankfurt (EU)** region —
   data-protection requirement).
2. Install the CLI (https://supabase.com/docs/guides/local-development) and convert
   `app/db.py`'s SCHEMA string into a migration file (`supabase migration new init`,
   paste, adjust types: SQLite `INTEGER PRIMARY KEY` → `bigint generated always as identity`).
3. Replace the `sqlite3` connection in `app/db.py` with Postgres via `psycopg`
   (connection string from the Supabase dashboard). The queries are plain SQL and
   already compatible except `json_extract(...)` → `->>` operator (three call sites).
4. What Supabase adds beyond the database: Auth (login for volunteers/staff),
   Row Level Security (per-tier access rules from the full plan), Realtime
   (live Radar updates), Storage (Studio media), pgvector (real embeddings for
   claim matching instead of the word-overlap stand-in).

## Learn more
- Supabase in 100 seconds: https://www.youtube.com/watch?v=zBZgdTb-dns
- Row Level Security: https://supabase.com/docs/guides/database/postgres/row-level-security
