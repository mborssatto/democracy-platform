# Integration: X (Twitter) via EU Digital Services Act, Article 40

**Status in prototype: 🔴 NOT BUILT (by design)** — no mock connector. X is a
bonus source, never a dependency: its commercial API is unaffordable (~$5 per
1,000 posts read) and the free researcher route takes time.

## The route (no litigation, per our decision)

1. **Confirm eligibility**: Art. 40(12) DSA gives researchers — explicitly including
   researchers at not-for-profit organizations — the right to access publicly
   available platform data for research into systemic risks in the EU. No academic
   affiliation required for the public-data tier. A Berlin court (Kammergericht,
   Feb 2026) confirmed this right is directly enforceable by NGOs.
2. **Prepare the application dossier** (this is what decides success):
   - research questions tied to systemic risks (disinformation & civic discourse),
   - precise data specification (fields, volumes, time ranges — mirror our watchlists),
   - data-protection plan (our DPIA, pseudonymization, retention),
   - independence & funding disclosure, publication commitment.
3. **Submit via X's DSA researcher page**: https://help.x.com/en/rules-and-policies/dsa-vetted-researchers
   and register on the EU **DSA Data Access Portal**: https://data-access.dsa.ec.europa.eu/
4. **If X stalls**: we simply wait and operate without X (decision: no legal disputes).
   Escalation to the Digital Services Coordinator (Bundesnetzagentur) is a letter,
   not a lawsuit, and is optional.
5. **When granted**: X provides API access to public data. Add a `fetch_x()` to
   `app/connectors.py` mapping posts to the same item shape as the other connectors
   (that's the whole integration — the pipeline doesn't care where posts come from).

## Mock payload (shape to expect, for when access lands)
```json
{
  "data": [{
    "id": "1815500000000000001",
    "author_id": "pseudonymize-me",
    "text": "Die Briefwahl ist Betrug...",
    "created_at": "2026-07-14T18:22:00Z",
    "public_metrics": {"retweet_count": 890, "reply_count": 340,
                        "like_count": 2150, "impression_count": 184000}
  }]
}
```

## Learn more
- EU Commission FAQ on researcher access: https://algorithmic-transparency.ec.europa.eu/news/faqs-dsa-data-access-researchers-2025-07-03_en
- Practical explainer: https://www.techpolicy.press/unpacking-the-eus-digital-services-act-delegated-act-on-data-access-/
