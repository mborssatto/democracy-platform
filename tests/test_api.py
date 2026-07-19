"""API error-handling tests (M1.2): every failure path returns clean JSON, never
an HTML traceback, and conflict states use proper 4xx codes."""
import pytest
from fastapi.testclient import TestClient

from app import db, main, pipeline


@pytest.fixture()
def client(fresh_db):
    con = db.connect()
    pipeline.seed_reference_data(con)
    con.close()
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


def test_unknown_ingest_source_is_clean_404(client):
    r = client.post("/api/ingest/facebook")
    assert r.status_code == 404
    assert "Unknown source" in r.json()["detail"]


def test_pipeline_run_with_no_items_is_friendly(client):
    r = client.post("/api/pipeline/run")
    assert r.status_code == 200
    body = r.json()
    assert body["pending_before"] == 0
    assert "note" in body


def test_claim_missing_opportunity_404(client):
    r = client.post("/api/opportunities/9999/claim")
    assert r.status_code == 404


def test_claim_already_claimed_opportunity_409(client):
    client.post("/api/ingest/tiktok")
    client.post("/api/pipeline/run")
    opps = client.get("/api/opportunities").json()
    assert opps, "pipeline should create opportunities from the fixture data"
    opp_id = opps[0]["id"]
    assert client.post(f"/api/opportunities/{opp_id}/claim").status_code == 200
    r = client.post(f"/api/opportunities/{opp_id}/claim")
    assert r.status_code == 409
    assert "already claimed" in r.json()["detail"]


def test_draft_for_missing_opportunity_404(client):
    assert client.post("/api/opportunities/9999/draft").status_code == 404


def test_update_missing_draft_404(client):
    r = client.put("/api/drafts/9999", json={"text": "hallo"})
    assert r.status_code == 404


def test_mark_posted_before_gates_pass_409(client):
    client.post("/api/ingest/tiktok")
    client.post("/api/pipeline/run")
    opp_id = client.get("/api/opportunities").json()[0]["id"]
    client.post(f"/api/opportunities/{opp_id}/claim")
    draft = client.post(f"/api/opportunities/{opp_id}/draft").json()
    r = client.post(f"/api/drafts/{draft['draft_id']}/mark-posted",
                    json={"posted_url": "https://example.com/x"})
    assert r.status_code == 409


def test_tipline_rejects_empty_fields(client):
    r = client.post("/api/tipline", json={"url": "", "text": ""})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)


def test_brief_for_unknown_narrative_404(client):
    assert client.post("/api/briefs/does_not_exist").status_code == 404


def test_unexpected_error_returns_json_500(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(main.queue, "claim_next", boom)
    r = client.post("/api/pipeline/run")
    assert r.status_code == 500
    assert "detail" in r.json()  # JSON body, not an HTML traceback
