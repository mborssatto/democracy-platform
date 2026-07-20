"""Data-source connectors.

Every external platform is MOCKED in the prototype — the fixture files in
mock_data/ have exactly the shape the real APIs return, so swapping a mock for
the real integration means changing only the fetch call, not the pipeline.

>>> INTEGRATION POINT (TikTok):   docs/integrations/tiktok.md
>>> INTEGRATION POINT (Junkipedia): docs/integrations/junkipedia.md
>>> INTEGRATION POINT (X / DSA):  docs/integrations/x-dsa.md
>>> INTEGRATION POINT (Instagram / Meta Content Library): docs/integrations/meta-content-library.md
>>> INTEGRATION POINT (Fact-checks): docs/integrations/factcheck.md
>>> WORKING TODAY (no API needed): Zeeschuimer NDJSON import for TikTok/Instagram —
    docs/integrations/zeeschuimer.md

The tipline is NOT mocked — it's our own feature and fully works.
"""
import json
from pathlib import Path

from . import tiktok_client

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
    if tiktok_client.enabled():  # real API once TIKTOK_CLIENT_KEY/SECRET are set
        return tiktok_client.fetch_real()
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


def followers_band(count: int | None) -> str:
    """Band an exact follower count (we never store the exact number)."""
    if count is None:
        return "unknown"
    if count < 1_000:
        return "<1k"
    if count < 10_000:
        return "1k-10k"
    if count < 100_000:
        return "10k-100k"
    return "100k+"


def parse_zeeschuimer(ndjson_text: str) -> tuple[list[dict], int]:
    """Parse a Zeeschuimer browser-extension NDJSON export into connector items.

    Zeeschuimer (Digital Methods Initiative) captures public posts while a
    researcher browses TikTok/Instagram normally and exports one JSON object
    per line: {"source_platform": "...", "source_url": "...", "data": {raw
    platform payload}}. This is the standard research path when platform
    Research APIs are unavailable — no credentials stored (hard rule 1), no
    scraping infrastructure. See docs/integrations/zeeschuimer.md.

    Returns (items, skipped) — malformed or unsupported lines are counted,
    never guessed at (quarantine-over-guess convention).
    """
    items, skipped = [], 0
    for line in ndjson_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            platform = rec.get("source_platform", "") or ""
            data = rec.get("data") or {}
            if "tiktok" in platform:
                items.append(_zs_tiktok(data))
            elif "instagram" in platform:
                items.append(_zs_instagram(data))
            else:
                skipped += 1
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            skipped += 1
    return items, skipped


def _zs_tiktok(data: dict) -> dict:
    author = data.get("author") or {}
    handle = author.get("uniqueId") if isinstance(author, dict) else str(author)
    stats = data.get("stats") or {}
    views = int(stats.get("playCount", 0))
    return {
        "platform": "tiktok",
        "platform_item_id": str(data["id"]),
        "real_author_id": handle or "unknown",   # pseudonymized before storage
        "followers_band": followers_band((data.get("authorStats") or {}).get("followerCount")),
        "text": data.get("desc", ""),
        "text_en": None,
        # '@user' placeholder on purpose: raw handles never enter stored URLs
        # (TikTok resolves video URLs by id regardless of the username part).
        "url": f"https://www.tiktok.com/@user/video/{data['id']}",
        "posted_at": _epoch_iso(data.get("createTime")),
        "views": views,
        "views_prev": views,   # one-shot capture: velocity unknown → 0 growth
        "likes": int(stats.get("diggCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "shares": int(stats.get("shareCount", 0)),
    }


def _zs_instagram(data: dict) -> dict:
    user = data.get("user") or data.get("owner") or {}
    caption = data.get("caption")
    text = caption.get("text", "") if isinstance(caption, dict) else (caption or "")
    code = data.get("code") or data.get("shortcode") or ""
    # Photo posts have no view count; 0 keeps them below the amplification
    # floor (conservative: we never invent reach numbers).
    views = int(data.get("play_count") or data.get("view_count") or 0)
    return {
        "platform": "instagram",
        "platform_item_id": str(data.get("pk") or data.get("id") or code),
        "real_author_id": user.get("username", "unknown"),
        "followers_band": followers_band(user.get("follower_count")),
        "text": text,
        "text_en": None,
        "url": f"https://www.instagram.com/p/{code}/" if code else "",
        "posted_at": _epoch_iso(data.get("taken_at")),
        "views": views,
        "views_prev": views,
        "likes": int(data.get("like_count") or 0),
        "comments": int(data.get("comment_count") or 0),
        "shares": 0,
    }


def _epoch_iso(epoch) -> str:
    import time
    if not epoch:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(epoch)))


def load_factchecks() -> list[dict]:
    """MOCK fact-check corpus in ClaimReview-like shape (docs/integrations/factcheck.md).
    In production this table syncs from the Google Fact Check Tools API + GADMO partners,
    with full local snapshots (partners can disappear; our copies don't)."""
    return _load("factchecks.json")


def load_narratives() -> list[dict]:
    return _load("narratives.json")
