# Integration: Fact-check corpus (Google Fact Check Tools API + GADMO)

**Status in prototype: 🟡 MOCKED** — `mock_data/factchecks.json` contains five
clearly-marked mock entries in the style of Correctiv / dpa / AFP fact-checks.
URLs are placeholders (EXAMPLE) — they don't resolve.

## What it gives us
The claim-matching stage needs a library of published fact-checks. We NEVER write
fact-checks ourselves — we match posts to professionals' work and amplify it.

## Step-by-step: making it real

1. **Google Fact Check Tools API** (free, instant):
   - Get an API key at https://console.cloud.google.com/ (enable "Fact Check Tools API").
   - Query: `GET https://factchecktools.googleapis.com/v1alpha1/claims:search?query=briefwahl&languageCode=de&key=API_KEY`
   - Response contains `claims[].text`, `claimReview[].publisher.name`,
     `claimReview[].textualRating`, `claimReview[].url` — map to our `claims` table
     (`claim_de`, `fact_checker`, `verdict`, `url`).
2. **Sync job**: nightly, query the API for each active narrative's keywords, upsert
   into `claims`, and **store a full local snapshot** of each fact-check page
   (partners and APIs disappear — Google already removed fact-check features from
   Search in 2025; our copies must survive that).
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
