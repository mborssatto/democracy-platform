"""One-off migration (completes M3.1 for real): copy the local SQLite database
to the Postgres/Supabase target configured in .env.

WIPES the target's `public` schema first — run only with the owner's explicit
approval (given 2026-07-21: "Migrate to Supabase now" + "Set it up fresh").

Usage:
    .venv/bin/python scripts/migrate_sqlite_to_supabase.py --yes

Copies every table except `sessions` (users just log in again — tokens are
throwaway). Identity sequences are realigned after the copy so new inserts
don't collide with copied ids.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402  (needs ROOT on sys.path first)

SKIP_TABLES = {"sessions", "sqlite_sequence"}


def main() -> None:
    if "--yes" not in sys.argv:
        sys.exit("Refusing to run without --yes — this WIPES the target schema.")
    if db.backend() != "postgres":
        sys.exit("DB_BACKEND must be 'postgres' (via .env) so the target is the "
                 "configured Postgres/Supabase database.")

    src = sqlite3.connect(db.DB_PATH)
    src.row_factory = sqlite3.Row

    print(f"target: {db.database_url().split('@')[-1]}")  # host/db only, no creds
    con = db.connect()
    con.execute("DROP SCHEMA public CASCADE")
    con.execute("CREATE SCHEMA public")
    con.commit()
    con.close()

    db.init_db()  # creates all tables from migrations/001_init.sql

    con = db.connect()
    tables = [r["name"] for r in
              src.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
              if r["name"] not in SKIP_TABLES]
    for table in tables:
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: 0 rows")
            continue
        cols = rows[0].keys()
        # OVERRIDING SYSTEM VALUE: allow explicit ids into GENERATED ALWAYS
        # identity columns (harmless on BY DEFAULT ones like claims.id).
        override = " OVERRIDING SYSTEM VALUE" if "id" in cols else ""
        sql = (f"INSERT INTO {table} ({', '.join(cols)}){override} "
               f"VALUES ({', '.join('?' * len(cols))})")
        for row in rows:
            con.execute(sql, tuple(row[c] for c in cols))
        if "id" in cols:
            db.resync_identity(con, table)
        print(f"  {table}: {len(rows)} rows")
    con.commit()

    # verify: every copied table's count matches the source
    mismatches = []
    for table in tables:
        n_src = src.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        n_dst = con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        if n_src != n_dst:
            mismatches.append(f"{table}: sqlite={n_src} pg={n_dst}")
    con.close()
    src.close()
    if mismatches:
        sys.exit("MISMATCH after copy:\n  " + "\n  ".join(mismatches))
    print("verified: all table counts match — migration complete")


if __name__ == "__main__":
    main()
