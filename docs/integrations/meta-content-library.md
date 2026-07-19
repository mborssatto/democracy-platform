# Integration: Instagram via Meta Content Library

**Status in prototype: 🔴 NOT BUILT (by design)** — signals-only by contract, so
there is nothing to mock into `content_items`: raw Instagram posts may NOT be
copied into our own database under Meta's terms.

## What it gives us (and doesn't)
- Keyword search over public content of Instagram accounts **≥25k followers** (or verified).
- Analysis happens **inside Meta's environment** — no raw-data export.
- Accounts under 25k followers are invisible. We accept this blind spot openly
  (decision: no scraping).

## Step-by-step: making it real

1. **Apply** via the University of Michigan's ICPSR/SOMAR portal:
   https://www.icpsr.umich.edu/sites/somar/meta-content-library
   Eligibility: researchers at academic institutions OR not-for-profit organizations
   doing public-interest research. Review takes ~2–6 weeks.
2. Choose the tier: free web UI + Meta's Secure Research Environment, or the paid
   Virtual Data Enclave for programmatic work (ICPSR fee ~$4,450/yr ≈ €350/mo).
3. **Work signals-only**: run searches and aggregations inside their environment;
   bring OUT only aggregate numbers (posts/day per narrative, engagement trends).
4. **Wire it in**: create a small `narrative_signals` table
   (`narrative_code, date, platform, post_count, total_views`) and enter the
   aggregates (manually at first — that's honest for a prototype). The Radar can
   then show "Instagram trend: ▲" next to narratives without any raw IG content
   in our database.

## Mock payload (the aggregate we'd bring out)
```json
{"narrative_code": "sozialleistungen_migranten", "date": "2026-07-14",
 "platform": "instagram", "post_count": 42, "total_views_estimate": 890000}
```

## Learn more
- Official: https://transparency.meta.com/researchtools/meta-content-library/
- Honest practitioner review (Bellingcat): https://bellingcat.gitbook.io/toolkit/more/all-tools/meta-content-library
