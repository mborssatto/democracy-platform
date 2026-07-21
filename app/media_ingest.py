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


# Adapter registry (ADR 0001): platform → URL validator + acquisition function.
# Adding a platform = one adapter module exposing these two callables + one
# entry here; extraction, persistence and enrichment stay untouched.
ADAPTERS = {
    "tiktok": {"is_post_url": is_tiktok_post_url, "acquire": apify_tiktok.acquire},
}


def platform_for(url: str):
    """The platform whose adapter claims this URL, or None (→ invalid row)."""
    return next((p for p, a in ADAPTERS.items() if a["is_post_url"](url)), None)


# ---------------------------------------------------------- handle scrubbing
def scrub_author(text, real_author_id: str):
    """Remove the author's own handle (`@handle` or bare) from extracted text.

    Hard rule 5 for the media path: platforms watermark the author's @handle
    into the video itself, and captions repeat it — so OCR/ASR would write the
    raw handle straight into the pseudonymized tables and every downstream LLM
    call. Other handles mentioned in content are left alone: they are content,
    not the ingestion-boundary identity this rule protects."""
    if not text or not real_author_id:
        return text
    return re.sub(r"@?\b" + re.escape(real_author_id) + r"\b", "[author]",
                  text, flags=re.IGNORECASE)


def scrub_extraction(result: extract.ExtractionResult, real_author_id: str):
    """Re-build an ExtractionResult with the author handle scrubbed from every
    channel. Goes through build_result so merged_text stays canonical."""
    speech = [extract.SpeechSegment(scrub_author(s.text, real_author_id), s.start_s, s.end_s)
              for s in result.speech]
    on_screen = [extract.OnScreenText(scrub_author(t.text, real_author_id), t.source)
                 for t in result.on_screen_text]
    return extract.build_result(result.extractor, speech, on_screen,
                                scrub_author(result.caption, real_author_id),
                                result.has_speech)


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
    """The 'acquire_url' job: acquire → extract → scrub → insert → persist.
    Raises on failure so the queue's backoff/dead-letter handling applies.
    Caller (worker.process_job) commits."""
    platform = platform_for(url)
    if platform is None:
        raise ValueError(f"no ingestion adapter claims URL: {url}")
    adapter = ADAPTERS[platform]
    extractor = extract.get_extractor()
    new_items = refreshed = 0
    for ingest_item, media_post in adapter["acquire"]([url], MEDIA_DIR):
        result = scrub_extraction(
            extractor.extract(media_post), ingest_item["real_author_id"])
        item = dict(ingest_item)
        # merged_text becomes the analyzed text; caption-only posts fall back
        # to their (scrubbed) caption so `text` is never empty (NOT NULL column).
        item["text"] = result.merged_text or scrub_author(item["text"], item["real_author_id"])
        item_id = insert_content_item(con, item)
        if item_id is None:
            # Already ingested: this re-acquisition IS a fresh metrics
            # snapshot — roll the view counter (views_prev ← stored views,
            # views ← fresh count; SQL assignment RHS reads the OLD row on
            # both backends) so the views/24h growth pill becomes computable.
            # Analysis/extraction stay untouched: the content didn't change.
            con.execute(
                "UPDATE content_items SET views_prev = views, views=?, "
                "likes=?, comments=?, shares=? WHERE platform=? AND platform_item_id=?",
                (item["views"], item["likes"], item["comments"], item["shares"],
                 item["platform"], item["platform_item_id"]))
            refreshed += 1
            continue
        save_extraction(con, item_id, result)
        new_items += 1
    db.audit(con, "ingestion", "acquire_url",
             {"url": url, "new_items": new_items, "refreshed": refreshed,
              "extractor": extractor.__class__.__name__})
    return {"url": url, "new_items": new_items, "refreshed": refreshed}
