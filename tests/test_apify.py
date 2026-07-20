"""Apify TikTok acquisition tests — fully offline via httpx.MockTransport.

Proves the run→poll→dataset→download flow of app/apify_tiktok.py against the
recorded-shape fixture (mock_data/apify_tiktok_items.json), the mapping into
the ingest-item shape (identical to connectors.fetch_tiktok output), and the
MediaPost adapter for extraction.
"""
import json
from pathlib import Path

import httpx
import pytest

from app import apify_tiktok, extract

FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "mock_data" / "apify_tiktok_items.json")
    .read_text(encoding="utf-8")
)["items"]
VIDEO_ITEM, SLIDE_ITEM = FIXTURE[0], FIXTURE[1]

RUN_STARTED = {"data": {"id": "run-test-1", "status": "RUNNING", "defaultDatasetId": "ds-test-1"}}
RUN_DONE = {"data": {"id": "run-test-1", "status": "SUCCEEDED", "defaultDatasetId": "ds-test-1"}}

FAKE_MP4 = b"\x00\x00\x00 ftypmp42 fake-video-bytes"
FAKE_JPG = b"\xff\xd8\xff\xe0 fake-jpeg-bytes"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_testtoken")


@pytest.fixture
def transport():
    state = {"requests": [], "polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        path = request.url.path
        if path.endswith("/acts/clockworks~tiktok-scraper/runs"):
            return httpx.Response(201, json=RUN_STARTED)
        if path.endswith("/actor-runs/run-test-1"):
            state["polls"] += 1
            return httpx.Response(200, json=RUN_STARTED if state["polls"] < 2 else RUN_DONE)
        if path.endswith("/datasets/ds-test-1/items"):
            return httpx.Response(200, json=FIXTURE)
        if "/key-value-stores/" in path and "/records/video-" in path:
            return httpx.Response(200, content=FAKE_MP4, headers={"content-type": "video/mp4"})
        if "/key-value-stores/" in path and "/records/slide-" in path:
            return httpx.Response(200, content=FAKE_JPG, headers={"content-type": "image/jpeg"})
        return httpx.Response(404, json={"error": "not_found"})

    mock = httpx.MockTransport(handler)
    mock.state = state
    return mock


def test_disabled_without_token(monkeypatch):
    # empty value = flag off (and load_dotenv never overrides an existing var,
    # so the developer's real .env can't leak into this test)
    monkeypatch.setenv("APIFY_TOKEN", "")
    assert apify_tiktok.enabled() is False


def test_run_actor_request_shape_and_polling(token, transport):
    """Start-run body carries postURLs + download flags + named KV store; the
    client polls until SUCCEEDED, then reads the dataset."""
    with httpx.Client(transport=transport) as client:
        items = apify_tiktok.run_actor(
            client,
            ["https://www.tiktok.com/@dwnews/video/7662514861099863328"],
            poll_seconds=0,
        )

    start = next(r for r in transport.state["requests"] if r.method == "POST")
    assert start.url.params["token"] == "apify_api_testtoken"
    body = json.loads(start.content)
    assert body["postURLs"] == ["https://www.tiktok.com/@dwnews/video/7662514861099863328"]
    assert body["shouldDownloadVideos"] is True
    assert body["shouldDownloadSlideshowImages"] is True
    assert body["videoKvStoreIdOrName"] == apify_tiktok.KV_STORE_NAME
    assert transport.state["polls"] >= 2  # saw RUNNING at least once, then SUCCEEDED
    assert [i["id"] for i in items] == [VIDEO_ITEM["id"], SLIDE_ITEM["id"]]


def test_run_actor_failure_raises(token):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json=RUN_STARTED)
        return httpx.Response(
            200, json={"data": {"id": "run-test-1", "status": "FAILED", "defaultDatasetId": "ds-test-1"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(apify_tiktok.AcquisitionError):
            apify_tiktok.run_actor(client, ["https://..."], poll_seconds=0)


def test_download_video_media(token, transport, tmp_path):
    with httpx.Client(transport=transport) as client:
        paths = apify_tiktok.download_media(client, VIDEO_ITEM, tmp_path)
    assert len(paths) == 1
    p = Path(paths[0])
    assert p.suffix == ".mp4" and p.read_bytes() == FAKE_MP4
    assert VIDEO_ITEM["id"] in p.name


def test_download_slideshow_images_in_order(token, transport, tmp_path):
    with httpx.Client(transport=transport) as client:
        paths = apify_tiktok.download_media(client, SLIDE_ITEM, tmp_path)
    assert len(paths) == 2
    assert [Path(p).suffix for p in paths] == [".jpg", ".jpg"]
    assert "slide_1" in Path(paths[0]).name and "slide_2" in Path(paths[1]).name


def test_to_ingest_item_matches_connector_shape():
    """Apify item → the exact dict shape connectors.fetch_tiktok produces, so
    ingest_source() works unchanged (pseudonymization included)."""
    item = apify_tiktok.to_ingest_item(VIDEO_ITEM)
    assert item == {
        "platform": "tiktok",
        "platform_item_id": "7662514861099863328",
        "real_author_id": "dwnews",
        "followers_band": "100k+",
        "text": "How secure is Germany's mail-in voting really? #briefwahl #wahl",
        "text_en": None,
        "url": "https://www.tiktok.com/@dwnews/video/7662514861099863328",
        "posted_at": "2026-07-18T09:15:00.000Z",
        "views": 89000,
        "views_prev": 0,
        "likes": 5400,
        "comments": 320,
        "shares": 210,
    }


@pytest.mark.parametrize(
    "fans,band",
    [(500, "<1k"), (5000, "1k-10k"), (50000, "10k-100k"), (1200000, "100k+")],
)
def test_followers_band(fans, band):
    assert apify_tiktok.followers_band(fans) == band


def test_to_media_post_video(tmp_path):
    post = apify_tiktok.to_media_post(VIDEO_ITEM, [str(tmp_path / "v.mp4")])
    assert post.platform == "tiktok"
    assert post.post_id == "7662514861099863328"
    assert post.caption_text.startswith("How secure is Germany's")
    assert [a.type for a in post.assets] == ["video"]


def test_to_media_post_slideshow(tmp_path):
    paths = [str(tmp_path / "s_slide_1.jpg"), str(tmp_path / "s_slide_2.jpg")]
    post = apify_tiktok.to_media_post(SLIDE_ITEM, paths)
    assert [a.type for a in post.assets] == ["image", "image"]
    assert [a.local_path for a in post.assets] == paths
