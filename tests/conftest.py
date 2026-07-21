"""Shared fixtures.

`fresh_db` gives every DB test a clean database on whichever backend is
active: a throwaway temp-file SQLite db (the default — never the real
data/prototype.db), or a wiped `public` schema on the disposable Postgres
when the suite runs with DB_BACKEND=postgres.
"""
import os
import re

import pytest

from app import db, llm

# --------------------------------------------------------------- SAFETY GUARD
# fresh_db on the postgres backend DROPS SCHEMA public. That must only ever
# hit a disposable LOCAL database — never a hosted one (Supabase!). A real
# DATABASE_URL in the developer's .env leaks into tests via load_dotenv, so
# refuse loudly here instead of trusting the environment.
# (Added 2026-07-21 after the suite connected to a Supabase pooler from .env.)
_LOCAL_DB_HOSTS = ("localhost", "127.0.0.1", "::1", "host.docker.internal")


def is_local_postgres_url() -> bool:
    url = os.environ.get("DATABASE_URL", "")
    match = re.search(r"@([^@/]+?)(?::\d+)?/", url)
    return (match.group(1) if match else "") in _LOCAL_DB_HOSTS


def _refuse_remote_postgres() -> None:
    if db.backend() != "postgres":
        return
    url = os.environ.get("DATABASE_URL", "")
    match = re.search(r"@([^@/]+?)(?::\d+)?/", url)
    host = match.group(1) if match else ""
    if host not in _LOCAL_DB_HOSTS:
        raise pytest.UsageError(
            f"REFUSING to run tests against non-local Postgres host '{host}' — "
            "the fresh_db fixture DROPS SCHEMA public. Run with DB_BACKEND=sqlite "
            "(or point DATABASE_URL at a disposable local Postgres, e.g. the "
            "dp-postgres docker container)."
        )


_refuse_remote_postgres()


@pytest.fixture(autouse=True)
def _no_ambient_real_llm(request, monkeypatch):
    """The developer's .env may carry OPENROUTER_API_KEY for the live demo,
    but the suite must stay offline/mock unless a test opts in explicitly.
    test_llm_real.py is the deliberate exception (paid smoke tests, key-gated)."""
    if "test_llm_real" not in request.node.nodeid:
        monkeypatch.setattr(llm, "OPENROUTER_KEY", None, raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_ambient_apify(monkeypatch):
    """Same rule for APIFY_TOKEN (real paid actor runs + the ToS exception —
    the one thing the suite must never trigger by accident). Set to "" rather
    than delenv: load_dotenv() would re-read a deleted var from .env, but it
    never overrides an existing (even empty) one. apify_tiktok.enabled() is
    then False; a test that wants Apify sets its own fake token on top."""
    monkeypatch.setenv("APIFY_TOKEN", "")
    yield


def login_headers(client, username: str) -> dict:
    """Log a demo user in through the API (auth stub: username IS the
    credential) and return the Authorization header to send with requests."""
    r = client.post("/api/login", json={"username": username})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def wipe_postgres() -> None:
    """Drop and recreate the public schema — a clean slate between tests."""
    _refuse_remote_postgres()  # defense in depth: never wipe a hosted DB
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
