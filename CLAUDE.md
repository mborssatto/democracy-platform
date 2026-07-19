# CLAUDE.md — Democracy Platform

Counter-disinformation platform for a Berlin pro-democracy NGO. Three products on one
shared pipeline: social listening (Radar), creator briefs (Studio), and human-in-the-loop
reply drafting (Respond). This repo is the working prototype; the full design lives in
`docs/PLAN.md` and the prioritized next steps in `BACKLOG.md` — read both before large changes.

## Commands

```bash
pip install -r requirements.txt   # setup
python run.py                     # run → http://localhost:8400
python -m pytest tests/ -q        # tests (must stay green; run from repo root)
```

No build step: the frontend is a single Vue page (`frontend/index.html`, vendored Vue).

## Architecture (60 seconds)

- `app/main.py` — FastAPI endpoints + serves the frontend. One deployable.
- `app/pipeline.py` — the 6 enrichment stages: triage(+EN translation) → classify →
  embed(stand-in) → claim-match → score → opportunity. Stages are idempotent
  (`UNIQUE(item_id, stage)`); the prototype runs them synchronously (plan: pg queue).
- `app/llm.py` — ALL AI calls go through here. Mock by default; real model when
  `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is in `.env`. Mock and real must always return
  identical shapes. No LangChain or agent frameworks — keep this file thin.
- `app/gates.py` — the five hard gates (genuine-edit, neutrality, copypasta, dwell,
  rate cap). Compliance-critical: change only with tests.
- `app/pseudo.py` — keyed-HMAC pseudonymization at the ingestion boundary + audited
  re-id vault. The canary test (`tests/test_pipeline.py`) asserts raw author handles
  never leak into main tables — keep it passing.
- `app/connectors.py` — data sources. Each mock reads `mock_data/*.json` shaped exactly
  like the real API; `docs/integrations/*.md` has the step-by-step to make each real.
- `app/db.py` — SQLite schema, mirrors the production (Supabase/Postgres) tables on
  purpose. Swap guide: `docs/integrations/supabase.md`.

## Hard rules (architecture, not preference — never "improve" these away)

1. **No auto-posting, ever.** No posting integrations, no stored platform credentials,
   no code path that publishes. Humans post from their own accounts and self-report the URL.
2. **AI drafts must pass the edit gate** before becoming post-ready. Never weaken the
   gates or add bypasses outside the audited admin override pattern.
3. **Never generate fact-check verdicts.** Match and cite published fact-checks only.
   Drafts and briefs must be grounded exclusively on the `claims` table.
4. **Amplification floor stays**: content below the view floor is never surfaced as an
   opportunity.
5. **Pseudonymize before anything leaves the process** — especially before any LLM API
   call. The canary test enforces this; extend it when adding data paths.
6. **Neutrality**: no electoral asks or party references in any generated content
   (German charity law). The blocklist in `gates.py` is a floor, not a ceiling.

## Conventions

- Python: stdlib + FastAPI + httpx only; add dependencies reluctantly. Plain SQL, no ORM.
- Every sensitive action writes to `audit_log` (append-only — never UPDATE/DELETE it).
- All LLM prompts request structured JSON; validate before use; on failure, quarantine
  rather than guess.
- Mock data lives in `mock_data/` in real-API shapes; when adding a source, add the
  fixture + an integration doc in `docs/integrations/` in the same PR.
- Tests accompany any change to `gates.py`, `pipeline.py`, or `pseudo.py`.
- User-facing text: plain language, explain terms at first use, German content gets an
  English translation field (`text_en`, `*_en`).

## Owner context

Product owner: Mariana (non-expert on the domain — write docs/PR descriptions in plain
language). This project also serves as an interview case study, so keep the code
readable and the honest-about-what's-mocked framing intact.
