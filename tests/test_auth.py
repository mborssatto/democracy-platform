"""Auth + roles tests (M3.2, dev auth stub).

Acceptance: a responder cannot access watchlists (Radar/narratives), the audit
log, or other responders' drafts — and only sees their own claimed
opportunities. Staff/admin are unrestricted.
"""
import pytest
from fastapi.testclient import TestClient

from app import db, main, pipeline
from conftest import login_headers


@pytest.fixture()
def client(fresh_db):
    con = db.connect()
    pipeline.seed_reference_data(con)
    con.close()
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c  # NO default auth headers — each test logs in explicitly


def _add_responder(username: str) -> None:
    """A second responder for the scoping tests (only one is seeded)."""
    con = db.connect()
    con.execute(db.sql_insert_ignore("INTO users (username, role, created_at) VALUES (?,?,?)"),
                (username, "responder", db.now()))
    con.commit(); con.close()


def _seed_opportunities(client) -> dict:
    """Ingest + pipeline as staff so opportunities exist. Returns staff headers."""
    staff = login_headers(client, "staff")
    assert client.post("/api/ingest/tiktok", headers=staff).status_code == 200
    assert client.post("/api/pipeline/run", headers=staff).status_code == 200
    return staff


# ------------------------------------------------------------------ 401s
def test_no_token_is_401(client):
    r = client.get("/api/opportunities")
    assert r.status_code == 401
    assert "detail" in r.json()


def test_bad_token_is_401(client):
    r = client.get("/api/opportunities",
                   headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_unknown_username_cannot_login(client):
    r = client.post("/api/login", json={"username": "mallory"})
    assert r.status_code == 401


def test_state_is_public_and_echoes_user(client):
    assert client.get("/api/state").json()["user"] is None
    headers = login_headers(client, "responder")
    u = client.get("/api/state", headers=headers).json()["user"]
    assert u == {"username": "responder", "role": "responder"}


# ------------------------------------------------------------------ role gates
def test_responder_cannot_access_staff_endpoints(client):
    headers = login_headers(client, "responder")
    assert client.get("/api/radar", headers=headers).status_code == 403
    assert client.get("/api/audit", headers=headers).status_code == 403
    assert client.post("/api/ingest/tiktok", headers=headers).status_code == 403


def test_staff_can_see_radar_and_audit(client):
    headers = login_headers(client, "staff")
    assert client.get("/api/radar", headers=headers).status_code == 200
    assert client.get("/api/audit", headers=headers).status_code == 200


def test_admin_can_access_everything(client):
    headers = login_headers(client, "admin")
    assert client.get("/api/radar", headers=headers).status_code == 200
    assert client.get("/api/audit", headers=headers).status_code == 200
    assert client.post("/api/ingest/tiktok", headers=headers).status_code == 200
    assert client.post("/api/pipeline/run", headers=headers).status_code == 200
    assert client.get("/api/opportunities", headers=headers).status_code == 200


# ------------------------------------------------------------------ data scoping
def test_responder_sees_only_open_and_own_claimed(client):
    _seed_opportunities(client)
    _add_responder("responder2")
    a = login_headers(client, "responder")
    b = login_headers(client, "responder2")

    opps = client.get("/api/opportunities", headers=a).json()
    assert opps, "pipeline should create opportunities from fixture data"
    opp_id = opps[0]["id"]
    assert client.post(f"/api/opportunities/{opp_id}/claim", headers=a).status_code == 200

    # A sees their claimed opportunity, attributed to them.
    mine = [o for o in client.get("/api/opportunities", headers=a).json()
            if o["id"] == opp_id]
    assert mine and mine[0]["status"] == "claimed" and mine[0]["claimed_by"] == "responder"

    # B does NOT see A's claimed opportunity at all.
    assert all(o["id"] != opp_id
               for o in client.get("/api/opportunities", headers=b).json())

    # Staff still see everything, including A's claim.
    staff_view = client.get("/api/opportunities",
                            headers=login_headers(client, "staff")).json()
    assert any(o["id"] == opp_id and o["claimed_by"] == "responder" for o in staff_view)


def test_responder_cannot_touch_others_drafts(client):
    _seed_opportunities(client)
    _add_responder("responder2")
    a = login_headers(client, "responder")
    b = login_headers(client, "responder2")

    opp_id = client.get("/api/opportunities", headers=a).json()[0]["id"]
    client.post(f"/api/opportunities/{opp_id}/claim", headers=a)
    draft = client.post(f"/api/opportunities/{opp_id}/draft", headers=a).json()
    draft_id = draft["draft_id"]

    # B may not draft on A's opportunity, nor update or mark-posted A's draft.
    assert client.post(f"/api/opportunities/{opp_id}/draft", headers=b).status_code == 403
    r = client.put(f"/api/drafts/{draft_id}", json={"text": "hijack attempt"}, headers=b)
    assert r.status_code == 403
    assert client.post(f"/api/drafts/{draft_id}/mark-posted",
                       json={"posted_url": "https://x.example/1"}, headers=b).status_code == 403

    # A can update their own draft; staff are unrestricted.
    assert client.put(f"/api/drafts/{draft_id}", json={"text": "my own rework"},
                      headers=a).status_code == 200
    assert client.put(f"/api/drafts/{draft_id}", json={"text": "staff supervision edit"},
                      headers=login_headers(client, "staff")).status_code == 200

    # The denial landed in the audit log (as did the logins).
    con = db.connect()
    actions = [r["action"] for r in con.execute("SELECT action FROM audit_log").fetchall()]
    con.close()
    assert "access_denied" in actions and "login" in actions


def test_claim_writes_real_username_into_audit(client):
    _seed_opportunities(client)
    a = login_headers(client, "responder")
    opp_id = client.get("/api/opportunities", headers=a).json()[0]["id"]
    client.post(f"/api/opportunities/{opp_id}/claim", headers=a)
    con = db.connect()
    row = con.execute("SELECT actor FROM audit_log WHERE action='opportunity_claimed' "
                      "ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    assert row["actor"] == "responder"


def test_logout_invalidates_token(client):
    headers = login_headers(client, "admin")
    assert client.post("/api/logout", headers=headers).status_code == 200
    assert client.get("/api/opportunities", headers=headers).status_code == 401
    # login + logout are both audited
    staff = login_headers(client, "staff")
    actions = [a["action"] for a in client.get("/api/audit", headers=staff).json()]
    assert "login" in actions and "logout" in actions
