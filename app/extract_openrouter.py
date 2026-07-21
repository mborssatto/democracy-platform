"""OpenRouter media extraction — Gemini, hard-pinned to Google Vertex.

Prototype path for real extraction (enable with MEDIA_EXTRACTOR=openrouter +
OPENROUTER_API_KEY in .env). Raw media (video frames, slides, audio) is the
one thing we cannot pseudonymize, so WHERE it goes is locked down in code:

- `RAW_MEDIA_ALLOWED_PROVIDERS` is the allowlist — Google Vertex only.
- Every payload is built by `build_request()` and re-checked by
  `assert_routing_pins()` immediately before sending. There is no code path
  that POSTs an unpinned request; tampering raises `RoutingPinError`.
- Pins: provider order+only = allowlist, `allow_fallbacks: false`,
  `data_collection: "deny"`, ZDR-only endpoints (`zdr: true`).

Why base64 + Vertex: Gemini routed via AI Studio only accepts YouTube links
for video, so we send assets as base64 data URLs and pin routing to Vertex.
The platform caption is deliberately NEVER sent here — extraction of caption
text needs no model, and less leaves the process (hard rule 5).

Never route open-weight VLMs (Qwen etc.) through OpenRouter for raw media:
their provider pool is uncontrolled unless pinned. That path is local-only
by design — see app/extract_local.py.
"""
import base64
import json
import os
import re
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

from app import extract as ex

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.5-flash"

# The allowlist is CODE, not a comment — assert_routing_pins() enforces it on
# every outgoing payload. Widening it is a privacy decision, not a tweak.
RAW_MEDIA_ALLOWED_PROVIDERS = ("google-vertex",)

_MIME = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
    ".aac": "audio/aac",
}

_PROMPT = (
    "You are a media transcription and OCR engine. Extract from the attached "
    "media, without judging or fact-checking anything:\n"
    "1. `speech`: every spoken utterance as "
    '{"text", "start_s", "end_s"} (empty list if nothing is spoken — NEVER '
    "transcribe music, singing along, or lyrics as speech).\n"
    "2. `on_screen_text`: ALL visible text — overlays, burned-in captions, "
    "sticker text, meme text, slide text. For video use "
    '{"text", "first_seen_s", "last_seen_s"}; for slide images use '
    '{"text", "slide"} where `slide` is the 1-based number of the slide as '
    "attached, in order.\n"
    "3. `has_speech`: true only if actual human speech is present.\n"
    "Keep original language. Reply with ONLY valid JSON: "
    '{"speech": [...], "on_screen_text": [...], "has_speech": bool}'
)


class RoutingPinError(RuntimeError):
    """A request was about to leave without the mandatory Vertex/ZDR pins."""


class ExtractionError(RuntimeError):
    """Model reply unusable — caller should quarantine the post, not guess."""


def assert_routing_pins(payload: dict) -> None:
    """Refuse any payload that is not pinned to the raw-media allowlist.

    Called inside build_request() AND immediately before every POST, so no
    call site can construct or mutate its way around the pins.
    """
    provider = payload.get("provider")
    if not isinstance(provider, dict):
        raise RoutingPinError("payload has no provider routing block")
    allowed = list(RAW_MEDIA_ALLOWED_PROVIDERS)
    if provider.get("order") != allowed:
        raise RoutingPinError(f"provider order must be exactly {allowed}")
    if provider.get("only") != allowed:
        raise RoutingPinError(f"provider allowlist must be exactly {allowed}")
    if provider.get("allow_fallbacks") is not False:
        raise RoutingPinError("allow_fallbacks must be explicitly false")
    if provider.get("data_collection") != "deny":
        raise RoutingPinError('data_collection must be "deny"')
    if provider.get("zdr") is not True:
        raise RoutingPinError("zdr (zero data retention endpoints only) must be true")


def build_request(content_parts: list) -> dict:
    """The ONLY way to build an extraction payload — pins are baked in."""
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": content_parts}],
        "provider": {
            "order": list(RAW_MEDIA_ALLOWED_PROVIDERS),
            "only": list(RAW_MEDIA_ALLOWED_PROVIDERS),
            "allow_fallbacks": False,
            "data_collection": "deny",
            "zdr": True,
        },
    }
    assert_routing_pins(payload)
    return payload


def _data_url(path: str) -> str:
    ext = Path(path).suffix.lower()
    mime = _MIME.get(ext)
    if mime is None:
        raise ExtractionError(f"unsupported media file type: {path}")
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class OpenRouterExtractor(ex.MediaExtractor):
    """Routes per ASSET TYPE (never platform): each video/audio asset is one
    request; all image assets go batched, in slide order, in one request."""

    def __init__(self):
        load_dotenv()
        self._key = os.environ.get("OPENROUTER_API_KEY")
        if not self._key:
            raise RuntimeError(
                "MEDIA_EXTRACTOR=openrouter needs OPENROUTER_API_KEY in .env — "
                "refusing to fall back to mock silently"
            )
        # Startup assertion: fail loudly NOW if the pins ever go missing.
        assert_routing_pins(build_request([{"type": "text", "text": "startup probe"}]))

    def extract(self, post: ex.MediaPost, client: Optional[httpx.Client] = None) -> ex.ExtractionResult:
        if client is None:
            # generous timeout: a 60s TikTok video is ~10 MB of base64 upload
            # plus model processing time
            with httpx.Client(timeout=600) as own:
                return self.extract(post, client=own)

        speech, on_screen, has_speech = [], [], False

        # video + audio: one request per asset (timestamps are per-asset)
        for asset in post.assets:
            if asset.type not in ("video", "audio"):
                continue
            part_type = "video_url" if asset.type == "video" else "audio_url"
            parts = [
                {"type": "text", "text": _PROMPT},
                {"type": part_type, part_type: {"url": _data_url(asset.local_path)}},
            ]
            reply = self._send(client, build_request(parts))
            s, o, h = _parse_reply(reply)
            speech += s
            on_screen += o
            has_speech = has_speech or h

        # images: all slides batched, in order, one request
        image_assets = [a for a in post.assets if a.type == "image"]
        if image_assets:
            parts = [{"type": "text", "text": _PROMPT}]
            for asset in image_assets:
                parts.append(
                    {"type": "image_url", "image_url": {"url": _data_url(asset.local_path)}}
                )
            reply = self._send(client, build_request(parts))
            s, o, h = _parse_reply(reply)
            speech += s
            on_screen += o
            has_speech = has_speech or h

        return ex.build_result("openrouter", speech, on_screen, post.caption_text, has_speech)

    def _send(self, client: httpx.Client, payload: dict) -> dict:
        assert_routing_pins(payload)  # last line of defense before the wire
        resp = client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {self._key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        # The pins above only prove what we ASKED for; the response names the
        # provider that actually served the request — verify it post-hoc.
        # An ABSENT provider field also raises: silence is not evidence of
        # compliant routing, and raw media already left the process.
        # The field is a coarse display name (a real Vertex-served response
        # reports "Google", observed 2026-07-21), so check CONSISTENCY with the
        # allowlist slugs, not equality: "google" is consistent with
        # "google-vertex"; "deepinfra" (or silence) is not.
        served = str(data.get("provider") or "").strip().lower().replace(" ", "-")
        consistent = served and any(
            a == served or a.startswith(served + "-") or served.startswith(a)
            for a in RAW_MEDIA_ALLOWED_PROVIDERS
        )
        if not consistent:
            raise RoutingPinError(
                f"response reports serving provider {data.get('provider')!r}, "
                f"inconsistent with the raw-media allowlist {list(RAW_MEDIA_ALLOWED_PROVIDERS)}"
            )
        return data


def _parse_reply(reply: dict):
    """Validate the model's JSON into typed channels; quarantine on nonsense."""
    try:
        content = reply["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ExtractionError(f"unexpected OpenRouter response shape: {e}")
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ExtractionError("model reply contains no JSON object")
    try:
        data = json.loads(match.group(0))
        speech = [
            ex.SpeechSegment(s["text"], float(s["start_s"]), float(s["end_s"]))
            for s in data.get("speech", [])
        ]
        on_screen = []
        for t in data.get("on_screen_text", []):
            if "slide" in t:
                source = ex.SlideTextSource("slide", int(t["slide"]))
            else:
                source = ex.VideoTextSource(
                    "video", float(t["first_seen_s"]), float(t["last_seen_s"])
                )
            on_screen.append(ex.OnScreenText(t["text"], source))
        has_speech = bool(data["has_speech"])
    except (KeyError, TypeError, ValueError) as e:
        raise ExtractionError(f"model reply failed validation: {e}")
    return speech, on_screen, has_speech
