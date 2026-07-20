# Integration: Apify TikTok acquisition (prototype-only)

**Status in prototype: 🟢 BUILT, behind `APIFY_TOKEN`** — `app/apify_tiktok.py`
downloads specific TikTok posts (video or photo/slideshow) via the Apify actor
`clockworks/tiktok-scraper`, then hands them to media extraction
(`docs/integrations/media-extraction.md`) and the normal pipeline. The UI entry
point is "Add posts by URL" in ⚙️ Data (M5.1), and `POST /api/ingest/urls`.

## The honest part, up front

The Apify actor **scrapes** TikTok — that is not permitted by TikTok's terms of
service. Owner decision (Mariana, 2026-07-20): acceptable for a handful of
hand-picked posts in a research prototype, to demonstrate end-to-end analysis
before the official TikTok **Research API** access is approved. Consequences:

- The path is OFF unless `APIFY_TOKEN` is set, and it never runs on a schedule —
  URLs are submitted one by one by a human.
- The Research API (`docs/integrations/tiktok.md`) stays the production
  acquisition story; this module is expected to be **deleted or demoted to a
  fallback** once that approval lands.
- Deletion duty still applies: if a post disappears from TikTok, delete our
  copy (same rule as the Research API path).

## How it works

1. `POST /api/ingest/urls {"urls": [...]}` validates TikTok post URLs and
   enqueues one `acquire_url` job each (invalid rows are reported, not fatal).
2. The worker job (`app/media_ingest.py process_url`) calls
   `apify_tiktok.acquire()`: start actor run (postURLs + download flags, named
   key-value store `democracy-media`) → poll → read dataset → download media
   files to `data/media/`.
3. Media extraction runs (mock/openrouter/local per `MEDIA_EXTRACTOR`), and the
   item is inserted through the standard pseudonymize-at-the-boundary path.
   The item's `text` IS the merged channel document; structured channels land
   in `media_extractions` (shown in the Radar detail view).
4. Failures follow the queue's backoff → dead-letter flow, visible in ⚙️ Data.

## Setup

```
# .env
APIFY_TOKEN=apify_api_...        # console.apify.com → Settings → API tokens
MEDIA_EXTRACTOR=openrouter       # + OPENROUTER_API_KEY for real extraction
```

Cost: the actor bills ~$1.70 per 1,000 posts plus a small per-video download
fee — single posts cost cents. The `democracy-media` key-value store is named
so downloads survive Apify's 7-day default retention; clean it out via the
Apify console when done.

## Learn more

- Actor: https://apify.com/clockworks/tiktok-scraper
- Apify API: https://docs.apify.com/api/v2
