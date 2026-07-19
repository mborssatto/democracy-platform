# Integration: Fact-check corpus (Google Fact Check Tools API + GADMO)

**Status in prototype: 🟢 IMPLEMENTED (needs free API key)** — `app/factcheck.py`
syncs real published fact-checks from the Google Fact Check Tools API when
`FACTCHECK_API_KEY` is set (`POST /api/sync/factchecks`, or the "Sync fact-checks
(Google API)" button on the Data tab). Without a key, the endpoint runs in mock
mode: `mock_data/factchecks.json` (five clearly-marked mock entries in the style
of Correctiv / dpa / AFP; placeholder EXAMPLE URLs) stays as the fallback corpus.

## What it gives us
The claim-matching stage needs a library of published fact-checks. We NEVER write
fact-checks ourselves — we match posts to professionals' work and amplify it.

## Setup: get the free API key (FACTCHECK_API_KEY)

1. Go to https://console.cloud.google.com/ and create (or pick) a project.
2. In "APIs & Services", enable the **"Fact Check Tools API"**.
3. Under "Credentials", create an **API key**.
4. Add it to `.env`: `FACTCHECK_API_KEY=AIza...` (see `.env.example`).
5. Press "Sync fact-checks (Google API)" on the Data tab, or
   `curl -X POST localhost:8400/api/sync/factchecks`.

## How the sync works (`app/factcheck.py`)

1. **API**: for each keyword of each tracked narrative (`NARRATIVE_KEYWORDS` in
   `app/llm.py`), query
   `GET https://factchecktools.googleapis.com/v1alpha1/claims:search?query=<kw>&languageCode=de&key=API_KEY`.
   Response fields map to our `claims` table: `claims[].text` → `claim_de`,
   `claimReview[0].publisher.name` → `fact_checker`,
   `claimReview[0].textualRating` → `verdict` (normalized: "Falsch"/"False" →
   `false`, "Irreführend"/"Misleading" → `misleading`, anything with
   "Kontext"/"context" → `missing_context`, unknown ratings pass through
   lowercased), `claimReview[0].url` → `url`, `claimReview[0].reviewDate` →
   `published_at`.
2. **Upsert keyed on `url`** — re-running never duplicates — and **a full local
   snapshot** of the API record lands in `claims.snapshot_json` (partners and
   APIs disappear — Google already removed fact-check features from Search in
   2025; our copies must survive that). Synced rows carry
   `source='google_factcheck'`; the seeded fallback rows carry `source='mock'`.
   Every sync writes an `audit_log` entry. Production plan: run nightly (cron).
3. **GADMO** (the German/Austrian fact-checking alliance — Correctiv, dpa, AFP, APA):
   https://gadmo.eu/ publishes German-language fact-checks; approach them for a
   partnership/feed once the org relationship exists. Until then the Google API
   already indexes most of their output via the ClaimReview standard.

## Mock payload
`mock_data/factchecks.json` — same fields our `claims` table uses. The real API's
`textualRating` maps to our `verdict` (normalize: "Falsch" → false, etc.).

## Learn more
- API docs: https://developers.google.com/fact-check/tools/api
- ClaimReview standard: https://developers.google.com/search/docs/appearance/structured-data/factcheck
