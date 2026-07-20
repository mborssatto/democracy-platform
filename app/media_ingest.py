"""URL ingestion (M5.1) — one submitted post URL → analyzed content item.

The 'acquire_url' job body: acquire the post's media (Apify, prototype path),
run media extraction (app/extract.py), then insert the item through the same
pseudonymize-at-the-boundary path every other source uses. The item's `text`
IS the merged multi-channel document ([SPEECH]/[ON-SCREEN]/[SLIDE]/[CAPTION]),
so every downstream pipeline stage — triage, classify, narrative eval —
reasons over everything the post actually says, not just its caption. The
structured channels are kept in `media_extractions` for the Radar detail view.
"""
import json
import re
from pathlib import Path

from . import apify_tiktok, db, extract, pseudo, queue

MEDIA_DIR = Path(__file__).resolve().parent.parent / "data" / "media"

# @handle may contain letters, digits, dot, underscore; post kind video|photo
_TIKTOK_POST_RE = re.compile(
    r"^https?://(www\.)?tiktok\.com/@[\w.]+/(video|photo)/\d+/?(\?.*)?$"
)


def is_tiktok_post_url(url: str) -> bool:
    return bool(_TIKTOK_POST_RE.match(url.strip()))


def insert_content_item(con, item: dict):
    """Pseudonymize + insert one ingest-shaped item; enqueue enrichment.
    Returns the new item id, or None when the item already existed.
    (Shared: ingest_source() loops sources through this.) Caller commits."""
    pseudo_id = pseudo.pseudonymize(item["real_author_id"])
    pseudo.store_mapping(con, item["real_author_id"], pseudo_id)
    cur = con.execute(
        db.sql_insert_ignore(
            "INTO content_items "
            "(platform, platform_item_id, author_pseudo, author_followers_band, text, text_en, "
            " url, posted_at, ingested_at, views, views_prev, likes, comments, shares) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
        (item["platform"], item["platform_item_id"], pseudo_id, item["followers_band"],
         item["text"], item["text_en"], item["url"], item["posted_at"], db.now(),
         item["views"], item["views_prev"], item["likes"], item["comments"], item["shares"]),
    )
    if not cur.rowcount:  # duplicate (platform, platform_item_id)
        return None
    item_id = cur.lastrowid
    queue.enqueue(con, "enrich_item", {"item_id": item_id})
    return item_id


# ------------------------------------------------------------------ persistence
def save_extraction(con, item_id: int, result: extract.ExtractionResult) -> None:
    """Store the structured channels behind an item's merged_text. Caller commits."""
    speech = [{"text": s.text, "start_s": s.start_s, "end_s": s.end_s} for s in result.speech]
    on_screen = []
    for t in result.on_screen_text:
        src = t.source
        source = ({"type": "slide", "index": src.index} if src.type == "slide"
                  else {"type": "video", "first_seen_s": src.first_seen_s,
                        "last_seen_s": src.last_seen_s})
        on_screen.append({"text": t.text, "source": source})
    con.execute(
        db.sql_insert_ignore(
            "INTO media_extractions "
            "(item_id, extractor, has_speech, speech_json, on_screen_json, caption, "
            " merged_text, created_at) VALUES (?,?,?,?,?,?,?,?)"),
        (item_id, result.extractor, int(result.has_speech), json.dumps(speech),
         json.dumps(on_screen), result.caption, result.merged_text, db.now()),
    )


def load_extraction(con, item_id: int):
    """The stored extraction as a plain dict (JSON fields parsed), or None."""
    row = con.execute(
        "SELECT * FROM media_extractions WHERE item_id=?", (item_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "extractor": row["extractor"],
        "has_speech": bool(row["has_speech"]),
        "speech": json.loads(row["speech_json"]),
        "on_screen_text": json.loads(row["on_screen_json"]),
        "caption": row["caption"],
        "merged_text": row["merged_text"],
    }


# -------------------------------------------------------------------- job body
def process_url(con, url: str) -> dict:
    """The 'acquire_url' job: acquire → extract → insert → persist channels.
    Raises on failure so the queue's backoff/dead-letter handling applies.
    Caller (worker.process_job) commits."""
    extractor = extract.get_extractor()
    new_items = 0
    for ingest_item, media_post in apify_tiktok.acquire([url], MEDIA_DIR):
        result = extractor.extract(media_post)
        item = dict(ingest_item)
        # merged_text becomes the analyzed text; caption-only posts fall back
        # to their caption so `text` is never empty (NOT NULL column).
        item["text"] = result.merged_text or item["text"]
        item_id = insert_content_item(con, item)
        if item_id is None:
            continue  # already ingested earlier — idempotent re-run
        save_extraction(con, item_id, result)
        new_items += 1
    db.audit(con, "ingestion", "acquire_url",
             {"url": url, "new_items": new_items, "extractor": extractor.__class__.__name__})
    return {"url": url, "new_items": new_items}
