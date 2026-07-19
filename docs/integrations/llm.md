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

## Production notes (from the full plan)
- Pseudonymize BEFORE any text reaches the API (the prototype's canary test asserts this).
- Structured outputs (JSON schemas) everywhere; log every call's cost to `llm_calls`.
- Two providers wired = automatic fallback when one fails.
