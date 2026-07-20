"""URL ingestion + extraction persistence tests (M5.1, TikTok-via-Apify).

Network-free: Apify acquisition is monkeypatched with fixture pairs and
extraction runs on the MockExtractor. Proves the whole chain the overnight
run uses: URL → acquire → extract → pseudonymized content_items row whose
text IS merged_text → media_extractions row → enrich_item job queued.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import apify_tiktok, db, extract, main, media_ingest, pipeline, queue, worker
from conftest import login_headers

VIDEO_URL = "https://www.tiktok.com/@dwnews/video/7662514861099863328"


@pytest.fixture()
def client(fresh_db):
    con = db.connect()
    pipeline.seed_reference_data(con)
    con.close()
    with TestClient(main.app, raise_server_exceptions=False) as c:
        c.headers.update(login_headers(c, "staff"))
        yield c


def _fixture_pair():
    """(ingest_item, media_post) as apify_tiktok.acquire would return, with
    asset names that exist in the MockExtractor fixtures."""
    ingest_item = {
        "platform": "tiktok",
        "platform_item_id": "7662514861099863328",
        "real_author_id": "dwnews",
        "followers_band": "100k+",
        "text": "How secure is Germany's mail-in voting really? #briefwahl",
        "text_en": None,
        "url": VIDEO_URL,
        "posted_at": "2026-07-18T09:15:00.000Z",
        "views": 89000, "views_prev": 0,
        "likes": 5400, "comments": 320, "shares": 210,
    }
    media_post = extract.MediaPost(
        "tiktok", "7662514861099863328",
        "How secure is Germany's mail-in voting really? #briefwahl",
        (extract.MediaAsset("video", "data/media/speech_only.mp4"),),
    )
    return ingest_item, media_post


# ------------------------------------------------------------------ persistence
def test_media_extraction_roundtrip(fresh_db):
    result = extract.MockExtractor().extract(_fixture_pair()[1])
    con = db.connect()
    try:
        media_ingest.save_extraction(con, 42, result)
        con.commit()
        loaded = media_ingest.load_extraction(con, 42)
    finally:
        con.close()
    assert loaded["extractor"] == "mock"
    assert loaded["has_speech"] is True
    assert loaded["merged_text"] == result.merged_text
    assert loaded["speech"][0]["text"].startswith("Die Briefwahl wird massenhaft")
    assert loaded["caption"] == result.caption


# --------------------------------------------------------------------- endpoint
def test_ingest_urls_endpoint_queues_valid_rejects_invalid(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_test")
    r = client.post(
        "/api/ingest/urls",
        json={"urls": [VIDEO_URL, "https://example.com/not-tiktok", "gibberish"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert [row["status"] for row in body["results"]] == ["queued", "invalid", "invalid"]
    assert body["queued"] == 1

    con = db.connect()
    try:
        jobs = con.execute("SELECT * FROM jobs WHERE kind='acquire_url'").fetchall()
        assert len(jobs) == 1
        assert json.loads(jobs[0]["payload"])["url"] == VIDEO_URL
        audit = con.execute(
            "SELECT * FROM audit_log WHERE action='ingest_urls'"
        ).fetchall()
        assert len(audit) == 1
    finally:
        con.close()


def test_ingest_urls_without_apify_is_clean_503(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "")
    r = client.post("/api/ingest/urls", json={"urls": [VIDEO_URL]})
    assert r.status_code == 503
    assert "Apify" in r.json()["detail"]


# ------------------------------------------------------------------ job process
def _run_acquire_job(monkeypatch):
    monkeypatch.setenv("MEDIA_EXTRACTOR", "mock")
    monkeypatch.setattr(apify_tiktok, "acquire", lambda urls, dest, client=None: [_fixture_pair()])
    con = db.connect()
    try:
        pipeline.seed_reference_data(con)
        queue.enqueue(con, "acquire_url", {"url": VIDEO_URL})
        con.commit()
        job = queue.claim_next(con)
        worker.process_job(con, job)
        return con
    except Exception:
        con.close()
        raise


def test_acquire_url_job_full_chain(fresh_db, monkeypatch):
    con = _run_acquire_job(monkeypatch)
    try:
        item = con.execute("SELECT * FROM content_items").fetchone()
        assert item is not None
        # text IS the merged multi-channel document, ready for the pipeline
        assert "[SPEECH 0.0-4.2s]" in item["text"]
        assert "[CAPTION] How secure is Germany's" in item["text"]
        # pseudonymized at the boundary — the real handle never lands in main tables
        assert item["author_pseudo"] != "dwnews"
        assert "dwnews" not in item["author_pseudo"]

        stored = media_ingest.load_extraction(con, item["id"])
        assert stored["extractor"] == "mock" and stored["has_speech"] is True

        enrich_jobs = con.execute("SELECT * FROM jobs WHERE kind='enrich_item'").fetchall()
        assert len(enrich_jobs) == 1
        done = con.execute("SELECT status FROM jobs WHERE kind='acquire_url'").fetchone()
        assert done["status"] == "done"
    finally:
        con.close()


def test_acquire_url_job_is_idempotent(fresh_db, monkeypatch):
    """Same URL acquired twice → still one content item, one extraction."""
    con = _run_acquire_job(monkeypatch)
    try:
        queue.enqueue(con, "acquire_url", {"url": VIDEO_URL})
        con.commit()
        job = queue.claim_next(con)
        worker.process_job(con, job)
        items = con.execute("SELECT * FROM content_items").fetchall()
        assert len(items) == 1
        rows = con.execute("SELECT * FROM media_extractions").fetchall()
        assert len(rows) == 1
    finally:
        con.close()


def test_acquire_url_failure_lands_in_backoff(fresh_db, monkeypatch):
    def boom(urls, dest, client=None):
        raise apify_tiktok.AcquisitionError("actor died")

    monkeypatch.setattr(apify_tiktok, "acquire", boom)
    con = db.connect()
    try:
        queue.enqueue(con, "acquire_url", {"url": VIDEO_URL})
        con.commit()
        job = queue.claim_next(con)
        worker.process_job(con, job)
        row = con.execute("SELECT * FROM jobs WHERE kind='acquire_url'").fetchone()
        assert row["status"] == "queued"  # re-queued with backoff, not lost
        assert row["attempts"] == 1
        assert "actor died" in row["last_error"]
    finally:
        con.close()


# ---------------------------------------------------------------- url validation
@pytest.mark.parametrize(
    "url,ok",
    [
        (VIDEO_URL, True),
        ("https://www.tiktok.com/@dwnews/photo/7637212512785616161", True),
        ("http://tiktok.com/@a_b.c/video/123", True),
        ("https://www.tiktok.com/@user", False),
        ("https://youtube.com/watch?v=x", False),
        ("not a url", False),
    ],
)
def test_tiktok_url_validation(url, ok):
    assert media_ingest.is_tiktok_post_url(url) is ok
