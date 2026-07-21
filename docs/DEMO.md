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
4. 📡 Radar → "🎬 Analyzed media posts" → expand: timestamped speech
   transcript, on-screen text with time spans, caption — then the evaluation
   chips with the prompt version + sha256 (hover a chip for the rationale).

## Story arc that lands well (10 min)

1. **Radar** — narratives grouped by DSA-style categories + the pro-democracy
   section; expand Briefwahl-Betrug: posts, evaluation chips, growth.
2. **The live URL analysis** (start it early, harvest it later).
3. **The honest parts** (differentiator): triage that says "this is
   legitimate news, not disinformation" (the Elterngeld post); the ASR
   quirks left visible ("Suriksy"); mock-by-default architecture; the canary
   test; no auto-posting; no self-made fact-check verdicts — match published
   fact-checkers only.
4. **Auditability**: `app/prompts/narrative_eval_v1.md` is versioned; every
   verdict stores the prompt sha256; audit log tab shows every sensitive
   action append-only.
5. **Numbers if asked**: full overnight 3-post run ≈ €0.15; extraction is
   Gemini pinned to Google Vertex (zero-data-retention, no fallbacks —
   enforced in code, `app/extract_openrouter.py`).

## Do-not-trip list

- Don't run `pytest` bare during the demo — it refuses (by design) because
  `.env` points at hosted Postgres. Use `DB_BACKEND=sqlite python -m pytest tests/ -q`
  (156 pass, ~1 s — actually a nice thing to show).
- Port 8400 busy → another copy is running; `lsof -ti :8400 | xargs kill`.
- The Apify path is a prototype-only acquisition exception (owner decision,
  documented in `docs/integrations/apify.md`) — say so if asked; the
  production story is the TikTok Research API application (M2.2, code ready).
