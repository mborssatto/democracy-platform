"""API error-handling tests (M1.2): every failure path returns clean JSON, never
an HTML traceback, and conflict states use proper 4xx codes."""
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
        # Endpoints require auth since M3.2 — run this suite as staff.
        c.headers.update(login_headers(c, "staff"))
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


# ---------------------------------------------------- tipline routing (M7)
def test_tipline_tiktok_url_routes_to_video_analysis(client, monkeypatch):
    """A TikTok link needs no claim text: it goes through the full acquisition
    + extraction path — the LLM reads the content, not the tipper."""
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_test")
    r = client.post("/api/tipline",
                    json={"url": "https://www.tiktok.com/@user/video/123456"})
    assert r.status_code == 200
    assert "video analysis" in r.json()["note"]
    con = db.connect()
    try:
        jobs = con.execute("SELECT * FROM jobs WHERE kind='acquire_url'").fetchall()
        assert len(jobs) == 1
        # no thin tipline item created — the acquisition path inserts the real one
        assert con.execute("SELECT COUNT(*) c FROM content_items").fetchone()["c"] == 0
    finally:
        con.close()


def test_tipline_non_tiktok_requires_claim_text(client):
    r = client.post("/api/tipline", json={"url": "https://x.com/user/status/1"})
    assert r.status_code == 422
    assert "describe what the post claims" in r.json()["detail"]


def test_tipline_non_tiktok_with_text_still_works(client):
    r = client.post("/api/tipline",
                    json={"url": "https://x.com/user/status/1",
                          "text": "Behauptet Wahlbetrug bei der Briefwahl"})
    assert r.status_code == 200
    con = db.connect()
    try:
        item = con.execute("SELECT * FROM content_items WHERE platform='tipline'").fetchone()
        assert item is not None
    finally:
        con.close()


def test_tipline_tiktok_without_apify_is_clean_503(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "")
    r = client.post("/api/tipline",
                    json={"url": "https://www.tiktok.com/@user/video/123456"})
    assert r.status_code == 503


# ------------------------------------------- manual opportunity (ingest cards)
def _insert_item(views: int) -> int:
    con = db.connect()
    try:
        cur = con.execute(
            "INSERT INTO content_items (platform, platform_item_id, author_pseudo, "
            "author_followers_band, text, url, posted_at, ingested_at, views, views_prev) "
            "VALUES ('tiktok', ?, 'anon_test', '10k-100k', 'Testpost', "
            "'https://tiktok.com/@a/video/1', ?, ?, ?, 0)",
            (f"manual-{views}", db.now(), db.now(), views))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def test_manual_opportunity_created_claimed_drafted(client):
    """The ingest cards' Draft response button: any analyzed post above the
    floor can get an opportunity on demand, then the normal claim+draft flow."""
    item_id = _insert_item(views=50_000)
    r = client.post(f"/api/items/{item_id}/opportunity")
    assert r.status_code == 200 and r.json()["created"] is True
    opp_id = r.json()["id"]
    # idempotent: second call returns the same opportunity
    r2 = client.post(f"/api/items/{item_id}/opportunity")
    assert r2.json() == {"id": opp_id, "status": "open", "created": False}
    assert client.post(f"/api/opportunities/{opp_id}/claim").status_code == 200
    assert client.post(f"/api/opportunities/{opp_id}/draft").status_code == 200


def test_manual_opportunity_below_floor_refused(client):
    """Hard rule 4 holds even for manual requests: low reach is never amplified."""
    item_id = _insert_item(views=10)
    r = client.post(f"/api/items/{item_id}/opportunity")
    assert r.status_code == 409
    assert "amplification" in r.json()["detail"]
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) c FROM opportunities").fetchone()["c"] == 0
    finally:
        con.close()


def test_manual_opportunity_missing_item_404(client):
    assert client.post("/api/items/99999/opportunity").status_code == 404
