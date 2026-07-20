# Integration: Zeeschuimer manual capture (TikTok + Instagram, no API needed)

**Status: 🟢 WORKING TODAY** — `POST /api/import/zeeschuimer` accepts a Zeeschuimer
NDJSON export and feeds it straight into the normal pipeline. This is the primary
path to TikTok and Instagram content while Research-API access is out of reach
(TikTok application not possible for us; Meta Content Library pending approval).

## What it is

[Zeeschuimer](https://github.com/digitalmethodsinitiative/zeeschuimer) is a free
Firefox extension by the Digital Methods Initiative (University of Amsterdam),
built for exactly our situation: researchers who need platform data without API
access. While you browse TikTok or Instagram normally, it captures the public
posts the platform sends to your browser and lets you export them as NDJSON
(one JSON object per line). No account credentials are stored or shared, no
scraping infrastructure runs — the researcher sees only what any logged-in user
sees. This matches hard rule 1 (no stored platform credentials) by construction.

## Step-by-step

1. Install Firefox, then the Zeeschuimer extension (link above; also on the
   Firefox add-on store).
2. Open the extension panel, enable capture for `tiktok.com` and/or
   `instagram.com`.
3. Browse deliberately: search the narrative keywords (e.g. "briefwahl"), open
   hashtag pages, scroll the results. Every post the page loads is captured —
   the counter in the panel ticks up.
4. In the panel: **Export → .ndjson** for the platform's dataset.
5. In our app: **Data tab → "Import Zeeschuimer export"**, pick the file. Items
   are pseudonymized on arrival, deduplicated by platform+item id, and queued
   for enrichment like any other source.

Field mapping lives in `app/connectors.py → parse_zeeschuimer()`; sample export
lines in `mock_data/zeeschuimer_export.ndjson`.

## Honest limitations & duties

- **Sampling bias**: you capture what you browse — coverage depends on the
  operator's search discipline (keyword checklist per narrative recommended).
  Velocity (`views_prev`) is unknown for one-shot captures; growth shows as 0%
  until an item is re-captured later (re-import updates nothing today — a
  metrics-refresh job is future work).
- **Instagram photos have no view count** → they stay below the amplification
  floor by design; we never invent reach numbers.
- **Legal posture** (for the org's records): collection of publicly visible
  content by a human researcher for public-interest research; GDPR basis is
  legitimate interest with immediate pseudonymization + banded audience data;
  honor deletion by re-checking captured items periodically (same deletion-sync
  duty as the TikTok API plan). This is the method mainstream European
  disinformation researchers use (4CAT ecosystem); still, have the org's
  lawyer bless the protocol before scaling it.

## Alternatives considered

- **Junkipedia** (`docs/integrations/junkipedia.md`) — aggregates TikTok /
  Instagram / YouTube / Telegram; nonprofit-friendly API. Best structural
  complement: it does broad monitoring, Zeeschuimer does targeted deep dives.
- **Meta Content Library** (`docs/integrations/meta-content-library.md`) — the
  official Instagram research route; applied, waiting.
- **Commercial scrapers (Apify, Bright Data, …)** — violate platform ToS,
  legally risky for a charity, credentials/infrastructure liabilities. Not
  recommended; deliberately not implemented.
