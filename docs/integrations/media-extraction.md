# Integration: Media content extraction (video / slides / audio → text)

**Status in prototype: 🟢 BUILT, mock by default** — `app/extract.py` turns an
already-downloaded social post into ONE text document (`merged_text`) that the
downstream claim-extraction step can read. The mock extractor runs offline
against fixtures; two real extractors sit behind env flags (below).

## Why this exists — claims hide in three places

A TikTok or Instagram post can carry a claim in three independent channels:

1. **Speech** — someone says the claim out loud.
2. **On-screen text** — "mute" videos with music put the claim in text
   overlays; slide posts (TikTok photo mode, Instagram carousels) spread it
   across images.
3. **Caption** — the platform description text under the post.

The same post often says DIFFERENT things in each channel (the overlay claims
one thing, the caption walks it back, the voiceover adds a third). So the
channels are **additive — never either/or**: extraction reads all of them and
labels each line by where it came from:

```
[SPEECH 0.0-4.2s] Die Briefwahl wird massenhaft manipuliert...
[ON-SCREEN 0.5-6.0s] BRIEFWAHL = WAHLBETRUG
[SLIDE 1] Die Impfpflicht kommt zurück...
[CAPTION] Die Briefwahl ist sicher — Faktencheck im Kommentar.
```

`merged_text` is rendered by one shared, deterministic function
(`extract.render_merged_text`) — same channels in, same document out, always.
To keep the document readable, speech segments (which ASR splits by pauses,
not grammar) are merged until a sentence is complete, and speech and on-screen
overlays interleave in time order — but a channel switch only happens once a
sentence is over, never in the middle of one.

## The shapes: MediaPost in, ExtractionResult out

Ingestion adapters produce a platform-agnostic **`MediaPost`**: platform,
post_id, caption text, and an ordered list of assets
(`video` / `image` / `audio` files already on disk). TikTok's adapter is
`connectors.tiktok_media_post()`; an Instagram adapter arrives with Meta
Content Library approval (BACKLOG, Parked). **No scraping** — this module only
consumes files ingestion acquired legitimately.

Every extractor returns the same **`ExtractionResult`**: `speech` (timestamped
segments), `on_screen_text` (with video timestamps or 1-based slide indices),
`caption` (passed through), `has_speech`, `extractor`, `merged_text`.

Extractors route purely on **asset type, never platform name** — the test
suite proves an Instagram post with the same assets extracts identically to a
TikTok one. No fact-check verdicts are formed here (hard rule 3): extraction
only reads what the post says.

## The three extractors

| Extractor | When | How |
|---|---|---|
| `MockExtractor` | **default** | Canned fixtures (`mock_data/media_extractions.json`), offline, free — all tests run on this. |
| `OpenRouterExtractor` | `MEDIA_EXTRACTOR=openrouter` + `OPENROUTER_API_KEY` | Gemini via OpenRouter, pinned to Google Vertex. Prototype path. |
| `LocalExtractor` | `MEDIA_EXTRACTOR=local` | Silero VAD → faster-whisper; frames/slides → local VLM. Production path — wiring done, internals still TODO stubs. |

Asking for a real extractor that cannot run (e.g. no key) **fails loudly** —
it never falls back to mock silently, so fixture output can't masquerade as a
real extraction.

## WHY the VAD gate (LocalExtractor)

*VAD = voice activity detection: a small model that answers "is anyone
speaking in this second of audio?" — it doesn't transcribe anything.*

Whisper (the speech-to-text model) has a known failure mode: given music-only
audio, it confidently invents fluent sentences ("hallucinates"). A slideshow
with a trending song would come back with a fake transcript, and downstream
claim extraction would treat hallucinated text as something the post actually
said. That poisons everything after it.

So the rule is a **gate, not an optimization**: VAD runs first, and Whisper
only ever sees the segments VAD marked as speech. No speech segments → the
transcript is empty *by verdict*, and `has_speech` comes from VAD — never from
"the transcript is non-empty". Slideshow audio is usually music (VAD correctly
yields nothing), but sometimes carries a voiceover — the gate handles both
without special-casing.

## WHY the visual branch always runs

Speech and on-screen text are independent channels — a video with a full
voiceover can still flash a *different* claim as overlay text. So the visual
branch (frame sampling at ~1 fps for video, every slide for image posts, then
OCR by a local vision model) runs on **every** post, whether or not there was
speech. "We already have a transcript" is never a reason to skip it.

## WHY the OpenRouter routing pins

Raw media (video, frames, slides) is the one thing we cannot pseudonymize —
faces and voices ARE the content. Hard rule 5 (pseudonymize before anything
leaves the process) therefore becomes a **destination allowlist** for raw
media: local models, or Gemini pinned to Google Vertex — nothing else, ever.

The pins, enforced in code (`app/extract_openrouter.py`), on every request:

- **Provider allowlist = Google Vertex only** (`order` + `only`), with
  `allow_fallbacks: false` — OpenRouter may never reroute to another provider
  when Vertex is busy.
- **`data_collection: "deny"`** — providers that train on or retain inputs are
  excluded.
- **ZDR only** (`zdr: true`) — zero-data-retention endpoints; nothing is
  stored after the response.

`assert_routing_pins()` runs when the payload is built AND again immediately
before every POST; the extractor's constructor fails at startup if the pins go
missing. Tests strip each pin and assert the client refuses to send.
(Why base64 upload at all: Gemini routed via AI Studio only accepts YouTube
links for video, so files go inline as base64 data URLs, routed to Vertex.)

Two deliberate non-features: the platform **caption is never sent** to
OpenRouter (it's already text; no model needed, less leaves the process), and
**open-weight VLMs (Qwen etc.) are never routed through OpenRouter** for raw
media — their provider pool is uncontrolled unless pinned, so that path is
local-only by design.

`merged_text` itself is downstream input to the reasoning LLM — it goes
through the pseudonymization boundary (`app/pseudo.py`) like every other text,
and the canary test guards that path when claim extraction lands.

## Enabling the real paths

```
# .env — prototype path (OpenRouter → Gemini on Vertex)
MEDIA_EXTRACTOR=openrouter
OPENROUTER_API_KEY=sk-or-...

# .env — production path (all local; internals still TODO)
MEDIA_EXTRACTOR=local
```

## Learn more

- OpenRouter provider routing: https://openrouter.ai/docs/features/provider-routing
- OpenRouter ZDR: https://openrouter.ai/docs/features/zdr
- Silero VAD: https://github.com/snakers4/silero-vad
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Whisper hallucination on non-speech audio (background): https://arxiv.org/abs/2402.08021
