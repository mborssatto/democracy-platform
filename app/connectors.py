"""Data-source connectors.

Every external platform is MOCKED in the prototype — the fixture files in
mock_data/ have exactly the shape the real APIs return, so swapping a mock for
the real integration means changing only the fetch call, not the pipeline.

>>> INTEGRATION POINT (TikTok):   docs/integrations/tiktok.md
>>> INTEGRATION POINT (Junkipedia): docs/integrations/junkipedia.md
>>> INTEGRATION POINT (X / DSA):  docs/integrations/x-dsa.md
>>> INTEGRATION POINT (Instagram / Meta Content Library): docs/integrations/meta-content-library.md
>>> INTEGRATION POINT (Fact-checks): docs/integrations/factcheck.md

The tipline is NOT mocked — it's our own feature and fully works.
"""
import json
from pathlib import Path

MOCK_DIR = Path(__file__).resolve().parent.parent / "mock_data"


def _load(name: str) -> list[dict]:
    return json.loads((MOCK_DIR / name).read_text(encoding="utf-8"))


def fetch_tiktok() -> list[dict]:
    """MOCK of the TikTok Research API `/v2/research/video/query/` response.

    Real call (documented step-by-step in docs/integrations/tiktok.md):
        POST https://open.tiktokapis.com/v2/research/video/query/
        Authorization: Bearer <client_access_token>
        {"query": {"and": [{"operation":"IN","field_name":"hashtag_name",
                            "field_values":["briefwahl","wahlbetrug"]},
                           {"operation":"EQ","field_name":"region_code","field_values":["DE"]}]},
         "start_date":"20260701","end_date":"20260715","max_count":100}
    """
    raw = _load("tiktok_videos.json")
    return [
        {
            "platform": "tiktok",
            "platform_item_id": str(v["id"]),
            "real_author_id": v["username"],           # pseudonymized before storage
            "followers_band": v["followers_band"],
            "text": v["video_description"],
            "text_en": v.get("_translation_en"),        # mock-mode translation
            "url": f"https://www.tiktok.com/@user/video/{v['id']}",
            "posted_at": v["create_time"],
            "views": v["view_count"],
            "views_prev": v["view_count_24h_ago"],
            "likes": v["like_count"],
            "comments": v["comment_count"],
            "shares": v["share_count"],
        }
        for v in raw
    ]


def fetch_junkipedia() -> list[dict]:
    """MOCK of a Junkipedia list/feed export (docs/integrations/junkipedia.md)."""
    raw = _load("junkipedia_items.json")
    return [
        {
            "platform": "junkipedia",
            "platform_item_id": item["id"],
            "real_author_id": item["channel_name"],
            "followers_band": item.get("followers_band", "unknown"),
            "text": item["post_title"],
            "text_en": item.get("_translation_en"),
            "url": item["published_url"],
            "posted_at": item["published_at"],
            "views": item.get("engagement", {}).get("views", 0),
            "views_prev": item.get("engagement", {}).get("views_prev", 0),
            "likes": item.get("engagement", {}).get("likes", 0),
            "comments": item.get("engagement", {}).get("comments", 0),
            "shares": item.get("engagement", {}).get("shares", 0),
        }
        for item in raw
    ]


def fetch_tipline() -> list[dict]:
    """Seeded tipline submissions (the tipline itself is a real, working feature —
    the /api/tipline endpoint accepts new submissions from the UI)."""
    raw = _load("tipline_submissions.json")
    return [
        {
            "platform": "tipline",
            "platform_item_id": t["id"],
            "real_author_id": t.get("reported_account", "unknown"),
            "followers_band": t.get("followers_band", "unknown"),
            "text": t["text"],
            "text_en": t.get("_translation_en"),
            "url": t.get("url", ""),
            "posted_at": t["submitted_at"],
            "views": t.get("views_estimate", 0),
            "views_prev": 0,
            "likes": 0, "comments": 0, "shares": 0,
        }
        for t in raw
    ]


def load_factchecks() -> list[dict]:
    """MOCK fact-check corpus in ClaimReview-like shape (docs/integrations/factcheck.md).
    In production this table syncs from the Google Fact Check Tools API + GADMO partners,
    with full local snapshots (partners can disappear; our copies don't)."""
    return _load("factchecks.json")


def load_narratives() -> list[dict]:
    return _load("narratives.json")
