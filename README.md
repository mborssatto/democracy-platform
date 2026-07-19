# Democracy Platform — Prototype

A working, self-contained prototype of the counter-disinformation platform from the
engineering plan: **social listening → AI enrichment → human-in-the-loop response**,
in ~1,500 lines of Python + Vue.

Everything runs offline with zero accounts. Real platform integrations are mocked with
realistic fixture data; every mock has a step-by-step guide to make it real (see below).

![Radar](docs/screenshots/2-radar.png)

## Quickstart

```bash
pip install -r requirements.txt
python run.py
# open http://localhost:8400
```

Log in with one of the three demo users — **admin**, **staff**, or **responder**
(one click, no passwords: it's a dev auth stub, swapped for Supabase Auth in
production — see `docs/integrations/supabase.md`). Responders only get the
Respond flow, scoped to their own claimed opportunities; admin/staff see everything.

Optional: `cp .env.example .env` and add an `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`)
to switch every AI step from mock to a real model. Nothing else changes.

Run the tests: `python -m pytest tests/ -q` (14 tests: gates, pipeline, pseudonymization canary).

## The 3-minute demo script

1. **⚙️ Data tab** — click the three Ingest buttons, then **Run pipeline**. Watch the
   stage log: 13 posts in, the cheesecake decoy filtered out, fact-checks matched,
   opportunities created (and the 340-view post correctly NOT surfaced — amplification floor).
2. **📡 Radar** — narratives ranked by 24h growth. Toggle **View in English**.
3. **💬 Respond** — claim the top opportunity. Try posting the AI seed unchanged: blocked.
   Rewrite it in your own words: the edit gate passes, the 60-second review timer still
   holds, then everything goes green. Note there is no "post" button — you copy the text,
   post it yourself, and paste the link back.
4. **💬 Respond** — type "wählt nicht diese Partei" into a draft: the neutrality gate
   blocks it (German charity law: counter narratives, never campaign).
5. **🎨 Studio** — click a narrative: a creator brief with facts (from fact-checks only),
   angles, and what to avoid.
6. **📋 Audit** — every ingestion, gate decision, and vault access, append-only.

## Integration status

| Integration | Status | Guide |
|---|---|---|
| TikTok Research API | 🟡 mocked | [docs/integrations/tiktok.md](docs/integrations/tiktok.md) |
| Junkipedia | 🟡 mocked | [docs/integrations/junkipedia.md](docs/integrations/junkipedia.md) |
| Community tipline | 🟢 working | built-in (Data tab) |
| Fact-check corpus (Google API / GADMO) | 🟡 mocked | [docs/integrations/factcheck.md](docs/integrations/factcheck.md) |
| X via DSA Article 40 | 🔴 pending application | [docs/integrations/x-dsa.md](docs/integrations/x-dsa.md) |
| Instagram (Meta Content Library) | 🔴 signals-only by contract | [docs/integrations/meta-content-library.md](docs/integrations/meta-content-library.md) |
| Real AI models | 🟢 built (mock default) | [docs/integrations/llm.md](docs/integrations/llm.md) |
| Supabase (production DB) | 🟡 SQLite stand-in | [docs/integrations/supabase.md](docs/integrations/supabase.md) |

## What this prototype proves (and what it fakes)

**Real:** the shared data spine (one pipeline feeding three products), pseudonymization
at the ingestion boundary (keyed HMAC + audited vault, canary-tested), all five hard
gates (genuine-edit, neutrality, copypasta, review time, rate cap), transparent scoring
with the amplification floor, the no-auto-post architecture, the append-only audit log,
EN/DE translation layer.

**Real (since M3.3):** the job queue — ingestion enqueues one job per item into a
`jobs` table; a background worker drains it (`FOR UPDATE SKIP LOCKED` on Postgres)
with retry/backoff and a dead-letter view in the UI. The "Run pipeline" button
remains as a synchronous drain of the same queue.

**Real (since M4.2):** outcome measurement — after a reply is marked posted, snapshot
jobs re-check the thread at +1h/+24h/+72h and compute the thread-climate delta against
a projected no-intervention baseline (plan Ch. 9); the 📈 Outcomes tab shows it, with a
fast-forward button so demos don't wait three days.

**Faked, honestly:** platform data (fixtures), thread re-fetches for outcome snapshots
(deterministic mock of the same shape), auth (local session stub with three demo
roles — production uses Supabase Auth).
Each fake is the simplest stand-in with the same interface as the real thing.

## Repo map

```
run.py               one-command launcher
app/
  main.py            FastAPI endpoints
  db.py              SQLite schema (mirrors the plan's tables)
  pipeline.py        the 6 enrichment stages
  queue.py           job queue (SKIP LOCKED claim, backoff, dead-letter)
  worker.py          background worker thread + periodic ingest scheduler
  gates.py           the hard gates (edit, neutrality, copypasta, dwell, rate)
  llm.py             AI layer — mock by default, real with a key
  pseudo.py          HMAC pseudonymization + audited re-id vault
  connectors.py      data sources (mocked, with integration pointers)
frontend/index.html  single-page Vue app (vendored Vue, no build step)
mock_data/           realistic fixtures in real-API shapes
docs/integrations/   step-by-step guide per integration
tests/               gates + pipeline + pseudonymization canary
evals/               golden set + eval runner for the AI stages
```

## Evals

`evals/golden_set.json` is a fixed, hand-labeled sample of German posts — real
narrative examples, harmless decoys, and borderline cases — that acts as an answer
key for the AI triage and classify stages. Run `python evals/run_evals.py` to score
the current prompts (mock heuristics offline, the real model with an API key) and
get precision/recall per narrative; CI runs it on every push and fails if scores
drop below the thresholds. Each report is stamped with the prompt version, so you
can tell whether a prompt change made the AI better or worse.

## Learn how systems like this work

**The pick — [Agentic AI, Andrew Ng (DeepLearning.AI, 2025)](https://www.deeplearning.ai/courses/agentic-ai)**
(~10h, free to audit). It teaches exactly what this prototype is: decomposing a task into
LLM pipeline steps (classify → retrieve → draft → check → human review) and improving it
with evals. After it, every file in `app/` will read as an example of a named pattern.

Viewing order:
1. Foundation (1h): [Karpathy — Intro to Large Language Models](https://www.youtube.com/watch?v=zjkBMFhNj_g)
   (or the [3.5h Deep Dive](https://www.youtube.com/watch?v=7xTGNNLPyMI))
2. The core (~10h, do the code examples): [Ng — Agentic AI](https://www.deeplearning.ai/courses/agentic-ai)
3. Sharpen (2h): [Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
   + [Barry Zhang's talk](https://www.youtube.com/watch?v=D7_ipDqhtwk)
   + hands-on: [Building Systems with the ChatGPT API](https://www.deeplearning.ai/short-courses/building-systems-with-chatgpt/)
```
