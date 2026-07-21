# Interview demo guide (2026-07-22)

## Start (2 commands, ~10 seconds)

```bash
cd democracy-platform
.venv/bin/python run.py        # → http://localhost:8400  (uses Supabase via .env)
```

Log in as **staff**. Header badge should read `AI: real:openrouter`.

**If the venue wifi is down** (Supabase unreachable): start with the local
snapshot instead — browsing works fully offline, only live URL analysis needs
internet anyway:

```bash
DB_BACKEND=sqlite .venv/bin/python run.py
```

## The live moment: analyze a TikTok video in front of them

1. Pick a **fresh** TikTok post URL that morning (politics/news content demos
   best — e.g. anything recent from @dwnews). ⚠️ The three DW posts from
   2026-07-21 are already ingested — re-pasting them shows "0 new items"
   (idempotency, which is itself demoable).
2. ⚙️ Data → "Add posts by URL" → paste → **Analyze URLs**.
3. Talk over the wait (~3 min measured: ~2.5 min download + video
   extraction, ~25 s pipeline): the job queue is visible on the same tab —
   point at queued → running → done, and mention dead-letter + retry.
4. The result appears right there under **"Analyzed posts"** (same box): a
   card with the short link, category pills (hover for the AI's rationale),
   followers/views, the score strip with proportional bars (velocity/reach/
   fact-check/confidence against their real maxima), and the transcript
   behind the ▸ chevron — timestamped Speech / On-screen / Slide / Caption
   rows. High-scoring posts with an open opportunity get a live **Draft
   response** button that jumps into the Respond flow.
5. Same data also in 📡 Radar → "🎬 Analyzed media posts" with the prompt
   version + sha256 on the evaluation chips.

## Story arc that lands well (10 min)

1. **Radar** — narratives grouped by DSA-style categories + the pro-democracy
   section; expand Briefwahl-Betrug: posts, evaluation chips, growth.
2. **The live URL analysis** (start it early, harvest it later).
3. **The honest parts** (differentiator): triage that says "this is
   legitimate news, not disinformation" (the Elterngeld post); the ASR
   quirks left visible ("Suriksy"); mock-by-default architecture; the canary
   test — which since 2026-07-21 also covers the media path: TikTok
   watermarks the author's @handle into the video, so extraction scrubs it
   to `[author]` before anything is stored or sent to an LLM; no
   auto-posting; no self-made fact-check verdicts — match published
   fact-checkers only.
4. **Auditability**: `app/prompts/narrative_eval_v3.md` is versioned (v2 added
   the hate_speech category + the presence-vs-stance doctrine; v3 added the
   "understand before judging" step, so the DW memorial-scuffle video reads as
   BOTH hate-speech-present AND pro-democracy — its framing portrays the Nazi
   regime as wrong); every verdict stores the prompt sha256; audit log tab
   shows every sensitive action append-only.
5. **Numbers if asked**: full overnight 3-post run ≈ €0.15 (a single video
   ≈ €0.03–0.05 extraction + ~€0.01 reasoning).

## Which models parse the video, and why

Architecture: `docs/adr/0001-modular-media-ingestion.md` (peer-reviewed and
accepted 2026-07-21). Model choices per step:

- **Media extraction** (speech + on-screen text + slides):
  `google/gemini-2.5-flash` via OpenRouter, **hard-pinned to Google Vertex**.
  Why this model: one multimodal call does timestamped speech transcription
  AND OCR of overlays/slides — no separate Whisper + OCR stack to run for a
  prototype — at Flash-tier cost/latency. Why Vertex specifically: raw media
  is the one thing we cannot pseudonymize, and Vertex is the enterprise
  endpoint with zero-data-retention and `data_collection: deny`; the pins
  (`only: google-vertex`, `allow_fallbacks: false`) are enforced in code on
  every request AND re-verified against the response's serving-provider field
  (`app/extract_openrouter.py`). Gemini via AI Studio was rejected: it only
  accepts YouTube links for video, so we send base64 to Vertex instead.
- **Reasoning stages** are tiered by depth (`app/llm.py`): mechanical stages
  (triage, classify, translate) run on `anthropic/claude-haiku-4.5` — cheap,
  fast, reliable JSON. The INTERPRETIVE stages (narrative evaluation,
  summary/topic) run on `anthropic/claude-sonnet-5`, chosen empirically
  (3-prompt × 2-model judged matrix, 2026-07-21): reading a post's implicit
  framing — e.g. that commemorating anti-Nazi resistance portrays the regime
  as wrong — is where the small model kept failing (procedural readings,
  neutrality slips). All reasoning stages see only pseudonymized, scrubbed
  text.
- **Production path** (stubbed, `app/extract_local.py`): Silero VAD →
  faster-whisper for audio, frame sampling → local VLM for visuals — zero
  third parties; the VAD gate means music/mute videos never get hallucinated
  transcripts.
- **Default**: mock extractors/LLM — the whole system runs offline and free;
  real models are opt-in via `.env` (`MEDIA_EXTRACTOR=openrouter` + keys).

## Do-not-trip list

- Don't run `pytest` bare during the demo — it refuses (by design) because
  `.env` points at hosted Postgres. Use `DB_BACKEND=sqlite python -m pytest tests/ -q`
  (164 pass, ~1 s — actually a nice thing to show).
- Port 8400 busy → another copy is running; `lsof -ti :8400 | xargs kill`.
- The Apify path is a prototype-only acquisition exception (owner decision,
  documented in `docs/integrations/apify.md`) — say so if asked; the
  production story is the TikTok Research API application (M2.2, code ready).
- **Check status.apify.com before the demo.** Apify had a platform-wide
  outage on 2026-07-21 (actor runs stuck in READY, never scheduled). If it
  recurs, the live URL analysis will sit in the queue and retry with backoff
  (visible, nothing lost — that resilience is itself demoable, briefly), but
  have the already-analyzed posts ready as the fallback story.
