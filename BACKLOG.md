# BACKLOG — prioritized, Claude Code-ready

> **Status 2026-07-20: ALL tasks M1.1–M4.4 are DONE** (implemented, tested on both
> DB backends, merged to main). Only the "Parked" section below remains open —
> each item is blocked on an external decision or approval, not on code.

Each task is scoped to be one Claude Code session or less, with acceptance criteria.
Work top to bottom unless Mariana reprioritizes. Keep `python -m pytest tests/ -q` green.

## M1 — Hardening (do first)

**M1.1 Git + CI hygiene**
Init git, first commit, add a GitHub Actions workflow running pytest on push.
✓ `git log` shows clean initial commit; CI file exists and passes locally via `act` or by inspection.

**M1.2 Error handling & empty states**
API returns clean 4xx/5xx JSON errors; frontend shows friendly messages (e.g. pipeline run
with no items, claiming an already-claimed opportunity).
✓ No unhandled 500s when clicking through the UI in any order.

**M1.3 Real-LLM smoke test**
With `ANTHROPIC_API_KEY` set, add `tests/test_llm_real.py` (skipped when no key) asserting
each `llm.py` function returns the documented shape from the real API.
✓ `pytest -q` passes with and without a key.

**M1.4 Config module**
Move magic numbers (amplification floor, score threshold, gate thresholds, rate cap) into
`app/config.py`, loadable from env, displayed in the UI footer.
✓ Changing a threshold requires no code edit.

## M2 — First real integration (biggest demo upgrade per hour)

**M2.1 Google Fact Check Tools API (free, instant key)**
Implement the sync job from `docs/integrations/factcheck.md`: nightly-style
`POST /api/sync/factchecks` querying the real API per narrative keyword, upserting into
`claims` with local snapshots. Keep mock corpus as fallback when no key.
✓ With a key, real Correctiv/dpa/AFP fact-checks appear in claim matching.

**M2.2 TikTok connector behind a feature flag**
Implement the real client from `docs/integrations/tiktok.md` against recorded fixtures
(httpx mock transport in tests); enable via `TIKTOK_CLIENT_KEY/SECRET` env vars when the
Research API application is approved.
✓ Tests prove request/response mapping without network; flag off = current mock behavior.

**M2.3 Media content extraction (added 2026-07-20 — DONE same day)**
Turn a downloaded post (video / mute video / slides / audio + caption) into one merged
text document for claim extraction: `app/extract.py` (types + MockExtractor, default),
`app/extract_openrouter.py` (Gemini pinned to Google Vertex, ZDR, no fallbacks — behind
`MEDIA_EXTRACTOR=openrouter` + `OPENROUTER_API_KEY`), `app/extract_local.py` (VAD-gated
Whisper + local VLM; wiring done, internals TODO). Design + rationale:
`docs/integrations/media-extraction.md`.
✓ 7 fixture shapes covered incl. platform-agnosticism; merged_text deterministic;
  client refuses to send without routing pins; VAD gate proven (Whisper never runs
  when VAD finds no speech). Follow-up: implement LocalExtractor internals; wire
  merged_text into the pipeline's claim-extraction stage (through `pseudo.py`).

## M3 — Production shape

**M3.1 Supabase swap** — follow `docs/integrations/supabase.md`: migrations, psycopg,
`json_extract` → `->>`; keep SQLite working for local dev via a `DB_BACKEND` switch.
✓ Both backends pass the test suite.

**M3.2 Auth + roles** — Supabase Auth (or a simple session stub locally): roles
admin/staff/responder; responders see only their opportunities/drafts (tier system from
the plan, Chapter 8.2).
✓ A responder cannot access watchlists, audit log, or others' drafts.

**M3.3 Background worker** — replace the synchronous "Run pipeline" with the plan's
Postgres job queue (`FOR UPDATE SKIP LOCKED`, attempts/backoff, dead-letter state) +
a periodic scheduler. UI shows queue depth.
✓ Ingestion enqueues; worker drains; failures land in a visible dead-letter view.

## M4 — Product depth

**M4.1 Nuxt 3 migration** — port `frontend/index.html` to a proper Nuxt 3 + TypeScript
app (JD stack). Keep the zero-build page as `/demo` fallback.
**M4.2 Outcome snapshots** — store thread snapshots at +1h/+24h/+72h (mocked scheduler
locally), compute the thread-climate delta vs. the non-intervened baseline (plan Ch. 9).
**M4.3 Real embeddings** — replace word-overlap claim matching with pgvector +
`text-embedding-3-small` (or Voyage), keeping the word-overlap path as no-key fallback.
**M4.4 Golden-set evals** — versioned labeled sample in `evals/`; a script scoring
triage/classify precision+recall per prompt version; wire into CI (plan Ch. 2.4).

## M5 — Queued (added after the M1–M4 build-out)

**M5.1 URL submission in "Ingest data" (single + CSV bulk) — DONE 2026-07-21**
Implemented for TikTok via the Apify acquisition path (owner decision: prototype-only
scraping exception, see `docs/integrations/apify.md`): URLs (textarea or CSV upload)
→ `POST /api/ingest/urls` → `acquire_url` jobs → Apify download → media extraction →
pseudonymized item whose text is the merged channel document → pipeline incl. the
auditable narrative evaluation (M5.2). Dead-letter + per-row invalid feedback as specced.
Content hydration via URL alone (no Apify) remains as originally scoped below.

*Original spec (still the shape for the no-scraping variant):*
In the Data tab's "1 · Ingest data" section (`frontend-nuxt/components/DataTab.vue` +
`/demo` fallback), let staff add post URLs by hand: one URL via a text input, or many
via CSV upload (a `url` column; extra columns ignored; blank lines skipped).
Backend: `POST /api/ingest/urls` (staff role, like `/api/ingest/{source}`) parses
platform + `platform_item_id` from each URL (TikTok/Instagram/X patterns), dedupes
against `content_items`, inserts new items as source `url-submitted` and enqueues
`enrich_item` per new item — reusing the `ingest_source` insert/pseudonymize/audit path.
**No scraping (hard rule):** a URL alone never triggers fetching page content. Items
enter as thin records (url, platform, item id, submitted-by note); metadata/content
hydrate later only via approved APIs (e.g. TikTok Research API lookup when the flag is
on) or manual paste, tipline-style. Unknown author → no pseudonym mapping until real
data arrives.
✓ Single URL and a 3-row CSV (1 dupe, 1 invalid line) both give friendly per-row
  results (added / duplicate / invalid — no unhandled 500s, M1.2 style); `audit_log`
  row per batch; tests cover URL parsing per platform, dedupe, CSV edge cases
  (BOM, ;-separated, missing `url` column); works on both DB backends.

**M5.2 Auditable narrative evaluation + category Radar — DONE 2026-07-21**
Six-category evaluation (false info / illegal content / fundamental rights / civic
discourse / minors+minorities+public health / pro-democracy) via the VERSIONED prompt
`app/prompts/narrative_eval_v1.md` — sha256 stored with every verdict (enrichment stage
`narrative_eval`). No fact-check verdicts generated (hard rule 3): false_information
only records tracked-narrative matches. Radar groups narratives by category incl. a
Pro-democracy section; topics expand to show per-channel extraction (speech/on-screen/
caption) and the evaluation chips; narratives addable from the UI with a category.
✓ Prompt file party-neutral (tested); mock/real parity; quarantine on invalid output.

## M6 — Media-ingestion follow-ups (from ADR 0001 review, 2026-07-21)

Committed in `docs/adr/0001-modular-media-ingestion.md` (approved with comments);
each is small and independent.

**M6.1 Display-name scrub**
Scrub the author's display name (Apify `authorMeta.nickName` — often spoken aloud)
alongside the handle in `media_ingest.scrub_extraction`; carry it on `ingest_item`.
✓ Canary-style test with the display name in speech/overlay/caption.

**M6.2 Mock acquisition adapter**
Fixture-backed `acquire()` registered when no `APIFY_TOKEN`, so the URL-ingestion
feature (not just the suite) works offline — closes the mock-by-default gap.
✓ `/api/ingest/urls` demoable with zero keys.

**M6.3 Per-adapter `enabled()` readiness**
Move the endpoint's TikTok/Apify-specific 503 check into the adapter registry.
✓ Adding adapter #2 requires no endpoint change.

**M6.4 URL dedupe before acquisition**
Skip already-acquired URLs before paying for an Apify run + extraction;
also gives caption-only items an upgrade path to full extraction.
✓ Resubmitting an ingested URL costs no external calls.

**M6.5 Renderer contract enforcement**
`ExtractionResult` is directly constructible, so "all extractors MUST use
`render_merged_text`" is convention — add a contract test over registered
extractors (or build `merged_text` in `__post_init__`).
✓ A hand-built inconsistent result fails loudly.

## Parked (decisions needed first)

- X connector — waiting on DSA Art. 40 application outcome.
- Instagram signals table — waiting on Meta Content Library approval.
- Instagram ingestion adapter — a `MediaPost` producer for `app/extract.py` (like
  `connectors.tiktok_media_post`). Blocked on the same Meta Content Library approval;
  no scraping. Extraction itself is already platform-agnostic (fixture-proven), so
  this is adapter-only work.
- Messenger tipline (WhatsApp/Telegram) — Phase 2 per plan.
- C2PA media signing — arrives with Studio media generation (Phase 2).
