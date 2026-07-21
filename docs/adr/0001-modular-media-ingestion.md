# ADR 0001 — Modular media-ingestion pipeline (TikTok first)

- **Status:** **Accepted** 2026-07-21 — approved with comments after a
  three-round engineering review (round 1: adapter registry, author-handle
  scrubbing, media-path canary; round 2: registry test wiring + APIFY_TOKEN
  suite guard). Acceptance run completed the same day (see Verification).
- **Date:** 2026-07-21
- **Owner:** Mariana (product) / platform engineering
- **Scope:** How a social-media post URL becomes an analyzed content item. TikTok
  is the first concrete pipeline; the architecture must carry Instagram, YouTube
  Shorts, X, etc. without redesign.

## Context

Posts on short-video platforms carry their claim in up to **three independent
channels** that often say *different* things:

1. **Speech** — the spoken audio track;
2. **On-screen text** — burned-in overlays on video, or text on slideshow images;
3. **Caption** — the platform post text.

A pipeline that only reads captions misses most of the content (a muted video
with an on-screen claim has an empty caption). Downstream stages (triage,
narrative classification, auditable narrative evaluation, scoring) must reason
over **everything the post says**, and analysts must be able to see **which
channel** said what.

Constraints inherited from the platform's hard rules (CLAUDE.md):

- Pseudonymize at the ingestion boundary; raw author handles never reach main
  tables or LLM calls (canary-tested).
- Raw media cannot be pseudonymized, so **where it is sent** must be restricted
  in code, not by convention.
- Mock-by-default: the repo must run offline with zero keys; real integrations
  are opt-in via `.env`.
- Plain Python + FastAPI + httpx; no agent frameworks; plain SQL.

## Decision

A **four-layer pipeline** with one platform-specific layer and three
platform-agnostic ones. Each layer has a single canonical data shape at its
boundary, so adding a platform touches exactly one layer.

```
URL ──▶ 1. ACQUISITION (per platform)      apify_tiktok.py (prototype)
              │  produces (ingest_item, MediaPost)
              ▼
        2. EXTRACTION (per asset TYPE)     extract.py + Mock/OpenRouter/Local
              │  produces ExtractionResult (speech / on-screen / caption
              │  + canonical merged_text)
              ▼
        3. PERSISTENCE (shared)            media_ingest.py
              │  pseudonymize → content_items (text = merged_text)
              │  + media_extractions (structured channels)
              ▼
        4. ENRICHMENT (shared)             pipeline.py via job queue
                 triage → classify → embed → claim-match → score → opportunity
```

### 1. Acquisition — the only platform-specific layer

An acquisition adapter is one module per platform exposing two callables,
registered in `media_ingest.ADAPTERS` (`platform → {is_post_url, acquire}`):

- `is_post_url(url) -> bool` — URL validation, used by the API edge and by
  job dispatch (`media_ingest.platform_for(url)` resolves the adapter);
- `acquire(post_urls, dest_dir) -> list[(ingest_item, MediaPost)]` — download
  metadata + media files.

Neither the endpoint nor the job body names a platform module directly — both
go through the registry, so the dispatch layer never changes per platform.

Its two outputs are the contracts every other layer depends on:

- **`ingest_item`** (dict): the exact shape `connectors.fetch_*()` mocks
  produce — platform, `platform_item_id`, `real_author_id` (pseudonymized at
  insert, never before persisted elsewhere), coarse `followers_band` (never
  exact counts), caption text, url, timestamps, engagement counts.
- **`MediaPost`** (frozen dataclass): `platform`, `post_id`, `caption_text`,
  ordered `assets: tuple[MediaAsset]` where each asset is
  `{type: video|image|audio, local_path}`. Asset order is meaningful — image
  assets are slides 1..n.

**TikTok concretely:** prototype acquisition uses Apify's
`clockworks/tiktok-scraper` actor (start run → poll → dataset items → download
media). This is an explicitly documented scraping exception for hand-picked
demo posts, OFF unless `APIFY_TOKEN` is set; the production story is the TikTok
Research API with the same adapter contract (docs/integrations/apify.md,
docs/integrations/tiktok.md).

### 2. Extraction — routes on asset type, never on platform

`MediaExtractor.extract(MediaPost) -> ExtractionResult` with three
interchangeable implementations selected by `MEDIA_EXTRACTOR` env
(fail-loud if a real one can't run — no silent fallback to mock):

- **mock** (default): canned per-asset fixtures; offline, deterministic, free.
- **openrouter** (prototype): Gemini 2.5 Flash, **hard-pinned to Google Vertex**
  (`provider.only`, `allow_fallbacks:false`, `data_collection:"deny"`,
  ZDR-only), enforced by `assert_routing_pins()` on every outgoing payload —
  there is no code path that sends raw media unpinned — and verified
  **post-hoc**: the response's `provider` field is checked against the
  allowlist, so a routing violation raises instead of being trusted. Captions
  are never sent (no model needed; less leaves the process).
- **local** (production path, stub): Silero VAD → faster-whisper for audio,
  frame sampling → local VLM for visuals. Zero third parties.

Because extractors dispatch on **asset type** (`video`/`image`/`audio`), an
Instagram carousel and a TikTok photo post with the same asset shapes extract
identically — this is what makes layers 2–4 platform-agnostic.

`ExtractionResult` keeps channels **structured and labeled**
(`SpeechSegment{text,start_s,end_s}`, `OnScreenText{text, video-span | slide
index}`, caption, `has_speech` as the explicit VAD verdict) and renders one
deterministic **`merged_text`** document through a single shared renderer:

```
[SPEECH 0.0-4.2s] …      (chronological)
[ON-SCREEN 0.0-63.0s] …  (chronological)
[SLIDE 1] …              (slide order)
[CAPTION] …              (last)
```

All extractors MUST build `merged_text` via this renderer, so implementations
can only disagree on content, never format. Invalid model output raises
`ExtractionError` → the job fails visibly (quarantine, don't guess).

### 3. Persistence — one boundary for every source

`media_ingest.process_url()` inserts through the same
pseudonymize-at-the-boundary path as every other connector. The content item's
`text` **is** `merged_text` (falling back to the scrubbed caption so the NOT
NULL column never gets an empty document), so all six enrichment stages read
the full multi-channel document with zero changes. Structured channels persist
to `media_extractions` (1:1 with the item) for channel-level UI display.
Idempotency: `UNIQUE(platform, platform_item_id)` makes re-acquisition a no-op
**at the DB layer only** — submitting the same URL twice still pays a full
acquisition + extraction before hitting the constraint, and an item first
ingested caption-only is not upgraded to a full extraction (accepted for the
prototype; both noted as follow-ups).

**Author-handle scrubbing (hard rule 5 on the media path).** Pseudonymizing
the `real_author_id` column is not enough here: platforms watermark the
author's `@handle` into the video itself, and captions repeat it — so OCR/ASR
would write the raw handle straight back into the pseudonymized tables and
every downstream LLM call. `scrub_extraction()` therefore rewrites every
channel (speech, on-screen, caption) replacing the author's own handle
(`@handle` or bare, case-insensitive) with `[author]` before anything is
persisted, re-rendering `merged_text` through the canonical renderer. *Other*
handles mentioned in content are deliberately left intact: they are content,
not the ingestion-boundary identity. Accepted residual risks: a very short or
dictionary-word handle can over-scrub unrelated words; the author's *display
name* (often spoken aloud — available from acquisition metadata, a cheap
follow-up scrub) is not yet covered; and visual-only identity (the author's
face, logo) is out of scope for text scrubbing.
The canary now covers this path: a dedicated test ingests a post whose
speech, overlay and caption all contain a canary handle and asserts it never
reaches `content_items` or `media_extractions`.

### 4. Enrichment — unchanged

Acquisition enqueues `enrich_item`; the existing six-stage pipeline runs as for
any other item. URL ingestion runs as an `acquire_url` job on the existing
queue (retry with backoff, dead-letter after repeated failure, visible and
retryable in the UI) because acquisition is minutes of network I/O and must
not block a request thread. One documented deviation from the queue's default
transaction contract: the worker commits the claim *before* running
`acquire_url` (otherwise SQLite's single writer lock is held for minutes and
starves every other write), so a crash mid-job leaves it visibly `running`
until the startup reset requeues it, instead of auto-returning to the queue.

## Adding platform N (the test of modularity)

1. New module `app/<platform>_acquire.py` implementing the adapter contract
   (`is_post_url` + `acquire()` returning `(ingest_item, MediaPost)` pairs).
2. One registry entry in `media_ingest.ADAPTERS`.
3. Fixture + `docs/integrations/<platform>.md` in the same PR (repo convention).

Layers 2–4 are untouched. No extractor, schema, pipeline, or UI change.
(Known limits, accepted for now: the endpoint's 503 readiness check is still
TikTok/Apify-specific — a per-adapter `enabled()` belongs in the registry when
adapter #2 lands; and `merged_text` interleaves multiple video assets on one
unlabeled timeline, fine for TikTok's one-video posts but the labels need an
asset index before a multi-video platform is added.)

## Alternatives considered

- **Per-platform extraction** (a TikTok extractor, an Instagram extractor…):
  rejected — extraction concerns are per media *type*, not per platform;
  N platforms × M extractors is exactly the coupling this ADR avoids.
- **One combined "understand this post" multimodal call** returning a summary
  instead of labeled channels: rejected — analysts must audit which channel
  said what; downstream prompts need verbatim content, not a lossy summary;
  and a summary can't be regression-tested deterministically.
- **Framework orchestration (LangChain etc.):** rejected — repo convention is
  thin, inspectable stdlib+httpx code; the queue already provides retry
  semantics.
- **Unpinned multimodal routing via OpenRouter's provider pool:** rejected —
  raw media is the one thing we cannot pseudonymize; uncontrolled provider
  pools violate hard rule 5. Vertex-pinned Gemini (prototype) and local models
  (production) are the only two allowed destinations, enforced in code.
- **Storing raw media in the DB:** rejected — files land in `data/media/`
  (gitignored); the DB stores derived text + provenance only.

## Consequences

- **Positive:** platform growth is O(1 adapter); extraction quality can be
  upgraded (mock → openrouter → local) without touching acquisition or
  persistence; every analyzed item is auditable down to channel + timestamp;
  offline test suite covers the full chain via fixture adapters.
- **Negative / accepted:** Apify path is a terms-of-service exception, held
  narrow (hand-picked URLs, prototype-only, off by default) until Research API
  access lands; `merged_text` interleaving loses cross-channel simultaneity
  (acceptable: timestamps preserved in labels); one extraction request per
  video asset means long videos are bounded by model context (accept for
  short-video platforms; revisit for YouTube).
- **Follow-ups:** implement `LocalExtractor` for production; TikTok Research
  API adapter behind the same contract; a fixture-backed mock acquisition
  adapter so the URL-ingestion *feature* (not just the test suite) works
  offline, closing the gap with the mock-by-default rule; per-adapter
  `enabled()` readiness in the registry; dedupe/skip already-acquired URLs
  before paying for acquisition; upgrade path from caption-only items to full
  extraction; summary + topic enrichment stage for the ingest-card UI.

## Verification for this ADR

Acceptance = ingest one real post end-to-end through the prototype path and
show the parsed channels:
`https://www.tiktok.com/@dwnews/video/7664688068737125664`
(Apify acquisition → Vertex-pinned Gemini extraction → pseudonymized item →
six-stage enrichment), demonstrated in a fresh local database.

**Result (2026-07-21): passed.** 10 speech segments (German, timestamped),
~70 on-screen overlays (DW captions/watermarks, speaker names), caption —
merged into one labeled document; pseudonymized item (`anon_631d49e6a616`,
band `100k+`, 319.6k views); real triage/classify/narrative-eval
(pro-democracy applies, others don't, versioned prompt sha256 recorded);
score 84.5 → open reply opportunity. Two honest caveats: (1) Apify had a
platform-wide outage during the run, so the actor-run *start* was replayed
from the same day's successful run of the same actor for the same URL — every
other step (media download, extraction, scrub, insert, enrichment) ran live;
(2) the run caught a real bug in the new post-hoc provider check: OpenRouter
reports the coarse display name `"Google"`, not `"google-vertex"` — the job
correctly failed loud (RoutingPinError) instead of trusting it, and the check
now tests allowlist *consistency* rather than string equality.
