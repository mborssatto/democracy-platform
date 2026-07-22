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
    # Patch the REGISTRY entry, not apify_tiktok.acquire: process_url resolves
    # adapters through ADAPTERS, which captured the function at import time —
    # patching the module attribute would leave the real (network) path live.
    monkeypatch.setitem(media_ingest.ADAPTERS["tiktok"], "acquire",
                        lambda urls, dest, client=None: [_fixture_pair()])
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


def test_reacquisition_rolls_view_snapshot(fresh_db, monkeypatch):
    """Re-submitting a known URL is a metrics refresh: views_prev ← old views,
    views ← fresh count — which is what makes the ▲ views/24h growth pill
    computable for URL-ingested posts (two snapshots needed)."""
    con = _run_acquire_job(monkeypatch)
    try:
        fresh_item, fresh_post = _fixture_pair()
        fresh_item = dict(fresh_item, views=120_000)
        monkeypatch.setitem(media_ingest.ADAPTERS["tiktok"], "acquire",
                            lambda urls, dest, client=None: [(fresh_item, fresh_post)])
        queue.enqueue(con, "acquire_url", {"url": VIDEO_URL})
        con.commit()
        while (job := queue.claim_next(con)) is not None:  # enrich job first, then ours
            worker.process_job(con, job)
        item = con.execute("SELECT * FROM content_items").fetchone()
        assert item["views"] == 120_000
        assert item["views_prev"] == 89_000  # the first snapshot
        # analysis + extraction untouched (content unchanged)
        assert con.execute("SELECT COUNT(*) c FROM media_extractions").fetchone()["c"] == 1
    finally:
        con.close()


def test_acquire_url_failure_lands_in_backoff(fresh_db, monkeypatch):
    def boom(urls, dest, client=None):
        raise apify_tiktok.AcquisitionError("actor died")

    monkeypatch.setitem(media_ingest.ADAPTERS["tiktok"], "acquire", boom)
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


def test_analyzed_posts_endpoint_shows_extraction_regardless_of_narrative(
        client, fresh_db, monkeypatch):
    """URL-submitted posts must be visible with their full analysis even when
    they match no tracked narrative or get triaged out — the submitter needs
    to see what the system read in the video."""
    con = _run_acquire_job(monkeypatch)
    while (job := queue.claim_next(con)) is not None:  # drain the enrich job too
        worker.process_job(con, job)
    con.commit()
    con.close()

    r = client.get("/api/analyzed")
    assert r.status_code == 200
    posts = r.json()
    assert len(posts) == 1
    post = posts[0]
    assert post["extraction"]["extractor"] == "mock"
    assert post["extraction"]["has_speech"] is True
    assert post["triage"] is not None  # verdict shown even if triaged out
    assert "narrative_eval" in post
    assert "score" in post  # priority score + breakdown power the ingest cards
    assert post["summarize"]["summary"] and post["summarize"]["topic"]  # card header


# ----------------------------------------------------------------- canary/scrub
def test_media_path_scrubs_burned_in_author_handle(fresh_db, monkeypatch):
    """Pseudonymization canary for the media path (hard rule 5): platforms
    watermark the author's @handle into the video itself, so OCR/ASR would
    otherwise write the raw handle into the pseudonymized tables and every
    downstream LLM call. The scrub in process_url must catch every channel."""
    handle = "canary_dw_journalist"
    ingest_item, media_post = _fixture_pair()
    ingest_item = dict(ingest_item, real_author_id=handle,
                       text=f"Folgt @{handle} für mehr #briefwahl")

    class WatermarkedExtractor(extract.MediaExtractor):
        def extract(self, post):
            return extract.build_result(
                "mock",
                [extract.SpeechSegment(f"Hier ist {handle} mit den Fakten", 0.0, 3.0)],
                [extract.OnScreenText(f"@{handle}", extract.VideoTextSource("video", 0.0, 9.9))],
                ingest_item["text"], True)

    monkeypatch.setattr(extract, "get_extractor", lambda: WatermarkedExtractor())
    monkeypatch.setitem(media_ingest.ADAPTERS["tiktok"], "acquire",
                        lambda urls, dest, client=None: [(ingest_item, media_post)])
    con = db.connect()
    try:
        pipeline.seed_reference_data(con)
        queue.enqueue(con, "acquire_url", {"url": VIDEO_URL})
        con.commit()
        worker.process_job(con, queue.claim_next(con))

        item = con.execute("SELECT * FROM content_items").fetchone()
        assert handle not in json.dumps(dict(item), ensure_ascii=False)
        stored = media_ingest.load_extraction(con, item["id"])
        assert handle not in json.dumps(stored, ensure_ascii=False)
        # scrubbed to a placeholder, not silently deleted — analysts still see
        # THAT an author reference was there
        assert "[author]" in stored["merged_text"]
    finally:
        con.close()


def test_scrub_author_leaves_other_handles_alone():
    scrubbed = media_ingest.scrub_author(
        "Antwort von @dwnews an @some_other_user: stimmt nicht", "dwnews")
    assert "@dwnews" not in scrubbed
    assert "@some_other_user" in scrubbed


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
    # registry dispatch mirrors the validator (ADR 0001)
    assert media_ingest.platform_for(url) == ("tiktok" if ok else None)


def test_normalize_url_adds_missing_scheme():
    """A bare domain (address bar copies often omit https://) must still
    resolve to the same adapter — this was the actual cause of a real user's
    "no ingestion adapter" error on an otherwise-valid TikTok URL."""
    bare = "www.tiktok.com/@samigujjar.699/video/7662390006866103560"
    normalized = media_ingest.normalize_url(bare)
    assert normalized == f"https://{bare}"
    assert media_ingest.platform_for(normalized) == "tiktok"
    # already-schemed URLs pass through untouched
    assert media_ingest.normalize_url(VIDEO_URL) == VIDEO_URL


def test_ingest_urls_accepts_scheme_less_url(client, monkeypatch):
    """End-to-end: the /api/ingest/urls endpoint normalizes before validating,
    so the exact URL shape from the bug report gets queued, not rejected."""
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_test")
    r = client.post("/api/ingest/urls", json={
        "urls": ["www.tiktok.com/@samigujjar.699/video/7662390006866103560"]})
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] == 1
    assert body["results"][0]["status"] == "queued"
    assert body["results"][0]["url"].startswith("https://")
    assert isinstance(body["results"][0]["job_id"], int)


# ------------------------------------------------------------------- progress
def test_acquire_url_job_reports_progress_stages(fresh_db, monkeypatch):
    """The job's `stage` column advances through the acquisition pipeline so
    the UI can render a filling progress bar instead of a flat "queued" state
    for what can be a multi-minute job."""
    monkeypatch.setenv("MEDIA_EXTRACTOR", "mock")
    seen_stages = []
    real_set_stage = queue.set_stage

    def spy_set_stage(con, job_id, stage):
        seen_stages.append(stage)
        real_set_stage(con, job_id, stage)

    monkeypatch.setattr(queue, "set_stage", spy_set_stage)
    monkeypatch.setitem(media_ingest.ADAPTERS["tiktok"], "acquire",
                        lambda urls, dest, client=None: [_fixture_pair()])
    con = db.connect()
    try:
        pipeline.seed_reference_data(con)
        job_id = queue.enqueue(con, "acquire_url", {"url": VIDEO_URL})
        con.commit()
        job = queue.claim_next(con)
        worker.process_job(con, job)
        assert seen_stages == ["downloading", "extracting", "saving"]
        row = con.execute("SELECT status, stage FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["status"] == "done"
    finally:
        con.close()


def test_jobs_status_endpoint(client):
    """GET /api/jobs?ids=... is what the frontend polls for the progress bar."""
    con = db.connect()
    job_id = queue.enqueue(con, "acquire_url", {"url": VIDEO_URL})
    con.commit(); con.close()

    r = client.get(f"/api/jobs?ids={job_id}")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["status"] == "queued"
    assert jobs[0]["stage"] is None

    assert client.get("/api/jobs?ids=").json()["jobs"] == []
