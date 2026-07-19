"""Real TikTok Research API client — behind a feature flag.

Flag = both TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET present in the
environment (they arrive only once TikTok approves the Research API
application, see docs/integrations/tiktok.md). While the flag is off,
`connectors.fetch_tiktok()` keeps serving the mock fixture unchanged.

Every function takes an `httpx.Client` so tests can inject an
`httpx.MockTransport` and prove the request/response mapping offline.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
QUERY_URL = "https://open.tiktokapis.com/v2/research/video/query/"
QUERY_FIELDS = (
    "id,username,create_time,region_code,video_description,"
    "like_count,comment_count,share_count,view_count,hashtag_names"
)

# Hashtags we monitor (same ones the mock fixture simulates).
DEFAULT_KEYWORDS = ["briefwahl", "wahlbetrug", "impfpflicht", "digitalereuro"]

# Tokens are valid 2h — refresh after ~110 min to never send a stale one.
_TOKEN_TTL_SECONDS = 110 * 60
_MAX_PAGES = 3  # cap pagination: 3 pages x 100 videos is plenty per sync

# Module-level token cache: {"token": str, "expires_at": epoch-seconds}.
_token_cache: dict = {}


def enabled() -> bool:
    """Feature flag: real API only when both credentials are configured."""
    load_dotenv()
    return bool(os.environ.get("TIKTOK_CLIENT_KEY")) and bool(
        os.environ.get("TIKTOK_CLIENT_SECRET")
    )


def get_access_token(client: httpx.Client) -> str:
    """Client-credentials token (valid 2h); cached module-level until expiry."""
    if _token_cache.get("token") and time.time() < _token_cache.get("expires_at", 0):
        return _token_cache["token"]
    resp = client.post(
        TOKEN_URL,
        data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "grant_type": "client_credentials",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + _TOKEN_TTL_SECONDS
    return token


def query_videos(
    client: httpx.Client,
    keywords: list[str],
    region: str = "DE",
    days: int = 14,
    max_count: int = 100,
) -> list[dict]:
    """POST /v2/research/video/query/ — paginated via cursor/has_more (max 3 pages)."""
    token = get_access_token(client)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    body = {
        "query": {
            "and": [
                {
                    "operation": "IN",
                    "field_name": "hashtag_name",
                    "field_values": keywords,
                },
                {
                    "operation": "EQ",
                    "field_name": "region_code",
                    "field_values": [region],
                },
            ]
        },
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "max_count": max_count,
    }

    videos: list[dict] = []
    for _ in range(_MAX_PAGES):
        resp = client.post(
            QUERY_URL,
            params={"fields": QUERY_FIELDS},
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        videos.extend(data.get("videos", []))
        if not data.get("has_more"):
            break
        body["cursor"] = data.get("cursor")
    return videos


def fetch_real(client: httpx.Client | None = None) -> list[dict]:
    """Fetch + map to the exact shape connectors.fetch_tiktok() returns in mock mode."""
    own_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        raw = query_videos(client, DEFAULT_KEYWORDS)
    finally:
        if own_client:
            client.close()
    return [
        {
            "platform": "tiktok",
            "platform_item_id": str(v["id"]),
            "real_author_id": v["username"],  # pseudonymized before storage
            # Video query returns no follower counts (and we never store exact
            # counts anyway) — band stays unknown until an account-info sync.
            "followers_band": "unknown",
            "text": v["video_description"],
            "text_en": None,  # translation happens in the triage stage
            # "@user" on purpose: TikTok resolves by video id, and we keep the
            # raw handle out of stored URLs (pseudonymization boundary).
            "url": f"https://www.tiktok.com/@user/video/{v['id']}",
            "posted_at": _epoch_to_iso(v["create_time"]),
            "views": v["view_count"],
            # The API has no historical snapshot; first sync seeds prev=current
            # (velocity = 0), later syncs update it from our own stored copy.
            "views_prev": v["view_count"],
            "likes": v["like_count"],
            "comments": v["comment_count"],
            "shares": v["share_count"],
        }
        for v in raw
    ]


def _epoch_to_iso(epoch: int) -> str:
    """Epoch seconds (UTC) -> naive ISO string, same format as the mock fixture."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
