# Integration: Junkipedia

**Status in prototype: 🟡 MOCKED** — `fetch_junkipedia()` reads `mock_data/junkipedia_items.json`.

## What it gives us
Junkipedia (https://www.junkipedia.org/) is a monitoring platform built for civil-society
organizations (run by the Algorithmic Transparency Institute). It already collects
problematic content across 13+ platforms including Telegram and fringe networks —
exactly the coverage we can't build ourselves.

## Step-by-step: making it real

1. **Request access** as a civil-society organization: https://www.junkipedia.org/
   (contact form; describe the counter-disinformation mission). Access is free/low-cost
   for civil society; 100+ organizations use it.
2. Once onboarded, set up **Lists/feeds** in the Junkipedia UI: keyword lists matching
   our watchlists (e.g. Briefwahl narratives) and channel lists (known disinfo channels).
3. **Export options** (documented at https://docs.junkipedia.org/): the UI supports CSV
   export of list content; API access for programmatic pulls is granted per-organization —
   ask for it during onboarding. Field names in our mock mirror their post schema
   (id, channel, title, published_url, published_at, engagement).
4. **Wire it in**: replace `_load(...)` in `fetch_junkipedia()` with either
   (a) the API pull, or (b) a CSV-drop folder the team exports into weekly.
   Keep the same field mapping the mock already uses.

## Mock payload
See `mock_data/junkipedia_items.json` — one Telegram-style item per row with
engagement counts.

## Learn more
- What is Junkipedia: https://docs.junkipedia.org/what-is-junkipedia/introduction
- GIJN overview: https://gijn.org/stories/new-tools-monitoring-social-media-junkipedia/
