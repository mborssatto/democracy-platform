# Integration: Real AI models (Anthropic / OpenAI)

**Status in prototype: 🟢 BUILT, mock by default** — `app/llm.py` runs deterministic
mock logic with no key present, and calls the real model automatically when a key exists.

## Step-by-step: switching to real AI

1. Get a key: https://console.anthropic.com/ (Anthropic) or https://platform.openai.com/ (OpenAI).
2. `cp .env.example .env` and add ONE line:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
3. Restart (`python run.py`). The header badge switches from `AI: mock` to
   `AI: real:anthropic`. Same pipeline, same endpoints — now the triage, classification,
   translation, reply drafts, briefs, and the "substantive change" gate use the model.
4. Cost expectation for this prototype's data volume: well under €0.10 per full
   pipeline run (13 short posts through a small model).

## What each AI call does (mock ↔ real parity)
| Function | Mock behavior | Real behavior |
|---|---|---|
| `triage` | keyword rules | small model, JSON verdict |
| `translate` | uses fixture translations | model translates |
| `classify` | keyword scoring per narrative | model classifies into taxonomy |
| `draft_reply` | empathy template + fact-check link | model drafts, grounded on the fact-check only |
| `draft_brief` | assembled from fact-check fields | model writes the brief as JSON |
| `substantive_change` | new-word heuristic | model judges the human edit |

## Embeddings (claim matching, M4.3)

The claim-match stage uses real embedding vectors when an embedding key is set;
without one, mock mode keeps the honest word-overlap stand-in (same thresholds,
same shape — nothing breaks, matching is just cruder).

1. Add ONE of these to `.env` (OpenAI is preferred if both are set):
   ```
   OPENAI_API_KEY=sk-...     → model: text-embedding-3-small
   VOYAGE_API_KEY=pa-...     → model: voyage-3.5-lite
   ```
2. That's it. On the next pipeline run, claim texts are embedded once and cached
   in the `claim_embeddings` table; item texts are embedded in ONE batch call per
   run (vectors stay in memory — items churn, claims are the stable corpus).

- **Cost**: text-embedding-3-small ≈ $0.02 per 1M tokens — a full pipeline run
  on this prototype's data is a fraction of a cent.
- **Privacy** (hard rule 5): only pseudonymized post text and claim text are sent
  to the embedding API — never author fields. The canary test guards the invariant.
- **Scale path — pgvector**: cosine similarity runs in plain Python, which is
  honest and fast at this corpus size (dozens of claims). When the corpus
  outgrows in-memory cosine, add a vector column on the Postgres backend:
  ```sql
  CREATE EXTENSION IF NOT EXISTS vector;
  ALTER TABLE claim_embeddings ADD COLUMN embedding vector(1536);
  CREATE INDEX ON claim_embeddings USING hnsw (embedding vector_cosine_ops);
  ```
  Then claim search becomes `ORDER BY embedding <=> $1 LIMIT k` — same math,
  done by the database.

## Production notes (from the full plan)
- Pseudonymize BEFORE any text reaches the API (the prototype's canary test asserts this).
- Structured outputs (JSON schemas) everywhere; log every call's cost to `llm_calls`.
- Two providers wired = automatic fallback when one fails.
