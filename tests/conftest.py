"""Shared fixtures.

`fresh_db` gives every DB test a clean database on whichever backend is
active: a throwaway temp-file SQLite db (the default — never the real
data/prototype.db), or a wiped `public` schema on the disposable Postgres
when the suite runs with DB_BACKEND=postgres.
"""
import pytest

from app import db


def login_headers(client, username: str) -> dict:
    """Log a demo user in through the API (auth stub: username IS the
    credential) and return the Authorization header to send with requests."""
    r = client.post("/api/login", json={"username": username})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def wipe_postgres() -> None:
    """Drop and recreate the public schema — a clean slate between tests."""
    con = db.connect()
    # Fail loudly instead of hanging if a leaked connection still holds locks
    # (e.g. an endpoint that raised without closing its connection).
    con.execute("SET lock_timeout = '5s'")
    con.execute("DROP SCHEMA public CASCADE")
    con.execute("CREATE SCHEMA public")
    con.commit()
    con.close()


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    if db.backend() == "postgres":
        wipe_postgres()
    else:
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    yield
    if db.backend() == "postgres":
        wipe_postgres()  # leave no test data behind
