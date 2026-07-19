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

## Parked (decisions needed first)

- X connector — waiting on DSA Art. 40 application outcome.
- Instagram signals table — waiting on Meta Content Library approval.
- Messenger tipline (WhatsApp/Telegram) — Phase 2 per plan.
- C2PA media signing — arrives with Studio media generation (Phase 2).
