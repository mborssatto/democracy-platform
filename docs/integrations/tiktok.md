# Integration: TikTok Research API

**Status in prototype: 🟢 CODE READY (flag off — waiting on Research API approval)** —
the real client lives in `app/tiktok_client.py` and switches on automatically when
both `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` are set in `.env` (they arrive
with TikTok's approval, step 1 below). Until then `app/connectors.py → fetch_tiktok()`
keeps reading `mock_data/tiktok_videos.json`, which has the exact shape of a real
Research API response. The request/response mapping is proven offline by
`tests/test_tiktok.py` against the recorded fixture `mock_data/tiktok_api_response.json`.

## Enabling it (once approved)

Add to `.env` (see `.env.example`):

```
TIKTOK_CLIENT_KEY=aw...      # from the developer portal, after approval
TIKTOK_CLIENT_SECRET=...
```

No code change needed — `fetch_tiktok()` detects the credentials and calls the real API
(token cached ~110 min; queries paginated up to 3 pages of 100 videos).

## What it gives us
Search public TikTok videos by keyword/hashtag/region/date, plus comments and public
account info. Free for approved EU non-profit research organizations.

## Step-by-step: making it real

1. **Apply for access** at https://developers.tiktok.com/products/research-api/ —
   choose "not-for-profit research institution (EU)". You'll describe the research
   purpose (systemic-risk monitoring of political disinformation) and your organization.
   Expect a review period of several weeks; approval is discretionary.
2. On approval you receive a **client key + client secret** in the developer portal.
3. **Get an access token** (valid 2h, cache it):
   ```
   POST https://open.tiktokapis.com/v2/oauth/token/
   Content-Type: application/x-www-form-urlencoded
   client_key=...&client_secret=...&grant_type=client_credentials
   ```
4. **Query videos** — this is the call our mock simulates:
   ```
   POST https://open.tiktokapis.com/v2/research/video/query/?fields=id,username,create_time,region_code,video_description,like_count,comment_count,share_count,view_count,hashtag_names
   Authorization: Bearer <token>
   {
     "query": { "and": [
       { "operation": "IN", "field_name": "hashtag_name",
         "field_values": ["briefwahl", "wahlbetrug"] },
       { "operation": "EQ", "field_name": "region_code", "field_values": ["DE"] }
     ]},
     "start_date": "20260701", "end_date": "20260715", "max_count": 100
   }
   ```
5. **Wire it in**: ✅ done — `app/tiktok_client.py` implements steps 3–4 (token caching,
   query pagination, field mapping identical to the mock). Setting the two env vars is
   the only remaining step. Note: the video query returns no follower counts, so
   `followers_band` stays `"unknown"` until an account-info sync is added (we never
   store exact counts either way).
6. **Respect the limits**: 1,000 requests/day documented ceiling; content searchable
   ~48h after posting; sync deletions (if a video is gone, delete our copy — the
   prototype's weekly deletion-sync job in the full plan).

## Mock payload (what the real API returns)
See `mock_data/tiktok_videos.json`. Real responses wrap this in
`{"data": {"videos": [...], "cursor": ..., "has_more": ...}}`.

## Learn more
- Official getting started: https://developers.tiktok.com/doc/research-api-get-started
- Researcher walkthrough (Weizenbaum Institute): https://methodslab.weizenbaum-institut.de/2024/11/12/tutorial-when-and-how-to-use-the-official-tiktok-api/
