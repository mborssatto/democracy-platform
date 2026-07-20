"""TikTok acquisition via Apify — PROTOTYPE-ONLY path, behind APIFY_TOKEN.

Owner decision 2026-07-20 (Mariana): to demo end-to-end video analysis before
TikTok Research API approval arrives, specific post URLs may be acquired
through Apify's clockworks/tiktok-scraper actor. Honest framing: that actor
scrapes TikTok, which TikTok's terms don't permit — this path is for a
handful of hand-picked posts in the prototype, is OFF unless APIFY_TOKEN is
set, and the Research API remains the production acquisition story
(docs/integrations/tiktok.md). Details + trade-offs: docs/integrations/apify.md.

Flow: start actor run (postURLs + download flags, named key-value store) →
poll until it finishes → read dataset items → download the media files →
hand back (ingest_item, MediaPost) pairs. Ingest items have the exact shape
connectors.fetch_tiktok() produces, so ingest_source() — including
pseudonymize-at-the-boundary — works unchanged.
"""
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

from app import extract

APIFY_BASE = "https://api.apify.com/v2"
ACTOR = "clockworks~tiktok-scraper"
KV_STORE_NAME = "democracy-media"  # named store: files survive the 7-day default TTL

_POLL_TIMEOUT_S = 600  # actor runs on a few posts finish in ~1-2 min; be generous


class AcquisitionError(RuntimeError):
    """Actor run failed / timed out / returned nothing usable."""


def enabled() -> bool:
    load_dotenv()
    return bool(os.environ.get("APIFY_TOKEN"))


def _token() -> str:
    load_dotenv()
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise AcquisitionError("APIFY_TOKEN missing — Apify acquisition is off")
    return token


def run_actor(client: httpx.Client, post_urls: list, poll_seconds: float = 5) -> list:
    """Start a scraper run for `post_urls`, poll to completion, return dataset items."""
    token = _token()
    resp = client.post(
        f"{APIFY_BASE}/acts/{ACTOR}/runs",
        params={"token": token},
        json={
            "postURLs": list(post_urls),
            "resultsPerPage": len(post_urls),
            "shouldDownloadVideos": True,
            "shouldDownloadSlideshowImages": True,
            "shouldDownloadCovers": False,
            "shouldDownloadAvatars": False,
            "videoKvStoreIdOrName": KV_STORE_NAME,
        },
    )
    resp.raise_for_status()
    run = resp.json()["data"]

    deadline = time.time() + _POLL_TIMEOUT_S
    while run["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            raise AcquisitionError(f"Apify run {run['id']} still {run['status']} after {_POLL_TIMEOUT_S}s")
        time.sleep(poll_seconds)
        poll = client.get(f"{APIFY_BASE}/actor-runs/{run['id']}", params={"token": token})
        poll.raise_for_status()
        run = poll.json()["data"]

    if run["status"] != "SUCCEEDED":
        raise AcquisitionError(f"Apify run {run['id']} ended {run['status']}")

    items = client.get(
        f"{APIFY_BASE}/datasets/{run['defaultDatasetId']}/items",
        params={"token": token, "clean": "true"},
    )
    items.raise_for_status()
    return items.json()


_EXT_BY_MIME = {"video/mp4": ".mp4", "video/webm": ".webm",
                "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _download(client: httpx.Client, url: str, dest_stem: Path, fallback_ext: str) -> Optional[str]:
    """GET one media URL → file next to dest_stem; None if this URL didn't work."""
    params = {"token": _token()} if url.startswith(APIFY_BASE) else None
    try:
        resp = client.get(url, params=params, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200 or not resp.content:
        return None
    mime = resp.headers.get("content-type", "").split(";")[0].strip()
    path = dest_stem.with_suffix(_EXT_BY_MIME.get(mime, fallback_ext))
    path.write_bytes(resp.content)
    return str(path)


def download_media(client: httpx.Client, item: dict, dest_dir) -> list:
    """Download an item's media files → ordered local paths (slides in order).

    Candidate URLs are tried in preference order (Apify key-value store copy
    first, TikTok CDN address as fallback) because the exact fields vary by
    actor version.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    post_id = str(item["id"])

    if item.get("isSlideshow"):
        paths = []
        for n, link in enumerate(item.get("slideshowImageLinks", []), start=1):
            candidates = [link.get("downloadLink"), link.get("tiktokLink")]
            for url in filter(None, candidates):
                got = _download(client, url, dest / f"{post_id}_slide_{n}", ".jpg")
                if got:
                    paths.append(got)
                    break
        if not paths:
            raise AcquisitionError(f"no slideshow image downloaded for post {post_id}")
        return paths

    candidates = list(item.get("mediaUrls") or [])
    addr = (item.get("videoMeta") or {}).get("downloadAddr")
    if addr:
        candidates.append(addr)
    for url in candidates:
        got = _download(client, url, dest / f"{post_id}_video", ".mp4")
        if got:
            return [got]
    raise AcquisitionError(f"no video downloaded for post {post_id} (tried {len(candidates)} urls)")


def followers_band(fans: int) -> str:
    """Same coarse bands as the mock corpus — we never store exact counts."""
    if fans < 1_000:
        return "<1k"
    if fans < 10_000:
        return "1k-10k"
    if fans < 100_000:
        return "10k-100k"
    return "100k+"


def to_ingest_item(item: dict) -> dict:
    """Apify dataset item → the connectors.fetch_tiktok() dict shape, so
    ingest_source() (incl. pseudonymization + audit) works unchanged."""
    author = item.get("authorMeta") or {}
    return {
        "platform": "tiktok",
        "platform_item_id": str(item["id"]),
        "real_author_id": author.get("name", "unknown"),
        "followers_band": followers_band(int(author.get("fans", 0))),
        "text": item.get("text") or "",
        "text_en": None,  # translation happens in the pipeline's triage stage
        "url": item.get("webVideoUrl") or "",
        "posted_at": item.get("createTimeISO") or "",
        "views": int(item.get("playCount", 0)),
        "views_prev": 0,  # single snapshot — no 24h-ago value on first sight
        "likes": int(item.get("diggCount", 0)),
        "comments": int(item.get("commentCount", 0)),
        "shares": int(item.get("shareCount", 0)),
    }


def to_media_post(item: dict, local_paths: list) -> extract.MediaPost:
    """Apify item + downloaded files → canonical MediaPost for extraction."""
    assets = []
    for path in local_paths:
        ext = Path(path).suffix.lower()
        kind = "video" if ext in (".mp4", ".mov", ".webm") else "image"
        assets.append(extract.MediaAsset(kind, path))
    return extract.MediaPost(
        platform="tiktok",
        post_id=str(item["id"]),
        caption_text=item.get("text") or None,
        assets=tuple(assets),
    )


def acquire(post_urls: list, dest_dir, client: Optional[httpx.Client] = None) -> list:
    """One-call acquisition: URLs → [(ingest_item, media_post), ...]."""
    if client is None:
        with httpx.Client(timeout=60) as own:
            return acquire(post_urls, dest_dir, client=own)
    pairs = []
    for item in run_actor(client, post_urls):
        paths = download_media(client, item, dest_dir)
        pairs.append((to_ingest_item(item), to_media_post(item, paths)))
    return pairs
