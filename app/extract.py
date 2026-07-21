"""Media content extraction — turn a downloaded social post into one text document.

A post can carry a claim in three independent channels: spoken audio, on-screen
text (overlays / slide text), and the platform caption. The same post often
says DIFFERENT things in each, so the channels are additive — extraction reads
all of them and renders one labeled `merged_text` document, the single input
for the downstream claim-extraction step.

Three interchangeable extractors implement `MediaExtractor`:

- `MockExtractor` (DEFAULT, repo convention: mock by default) — canned per-asset
  fixtures from `mock_data/media_extractions.json`, no network, no file reads.
- `OpenRouterExtractor` (`app/extract_openrouter.py`) — prototype path: Gemini
  via OpenRouter, hard-pinned to Google Vertex (see that module's allowlist).
- `LocalExtractor` (`app/extract_local.py`) — production path: Silero VAD →
  faster-whisper for audio, frame sampling → local VLM for visuals. Stub.

Extractors route purely on ASSET TYPE — never on the platform name. That is
what makes the module platform-agnostic: an Instagram carousel and a TikTok
photo post with the same asset shapes extract identically.

>>> INTEGRATION POINT: docs/integrations/media-extraction.md explains the
    channel design, the VAD gate, and the provider-pinning rationale.
"""
import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Union

from dotenv import load_dotenv

AssetType = Literal["video", "image", "audio"]
ExtractorName = Literal["mock", "openrouter", "local"]

_FIXTURES_PATH = Path(__file__).resolve().parent.parent / "mock_data" / "media_extractions.json"


# ------------------------------------------------------------------ input types
@dataclass(frozen=True)
class MediaAsset:
    type: AssetType
    local_path: str


@dataclass(frozen=True)
class MediaPost:
    """Canonical, platform-agnostic post shape produced by ingestion adapters
    (TikTok today — `connectors.tiktok_media_post`; Instagram later).
    Asset order matters: image assets are numbered as slides 1..n in order."""

    platform: str
    post_id: str
    caption_text: Union[str, None]
    assets: tuple  # tuple[MediaAsset, ...]


def media_post_from_dict(d: dict) -> MediaPost:
    return MediaPost(
        platform=d["platform"],
        post_id=d["post_id"],
        caption_text=d.get("caption_text"),
        assets=tuple(MediaAsset(a["type"], a["local_path"]) for a in d["assets"]),
    )


# ----------------------------------------------------------------- output types
@dataclass(frozen=True)
class SpeechSegment:
    text: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class VideoTextSource:
    type: Literal["video"]
    first_seen_s: float
    last_seen_s: float


@dataclass(frozen=True)
class SlideTextSource:
    type: Literal["slide"]
    index: int  # 1-based: slide 1 is the first image asset of the post


@dataclass(frozen=True)
class OnScreenText:
    text: str
    source: Union[VideoTextSource, SlideTextSource]


@dataclass(frozen=True)
class ExtractionResult:
    speech: tuple  # tuple[SpeechSegment, ...] — empty whenever has_speech is False
    on_screen_text: tuple  # tuple[OnScreenText, ...]
    caption: Union[str, None]  # passed through from MediaPost
    has_speech: bool  # the VAD verdict — NEVER derived from len(speech)
    extractor: ExtractorName
    merged_text: str


# ------------------------------------------------------------------ merged_text
# A sentence is over when the text ends in terminal punctuation (optionally
# followed by a closing quote/bracket). ASR segments are split by pauses, not
# grammar, so this is what lets us re-assemble fluid sentences.
_SENTENCE_END = re.compile(r"[.!?…](?:[\"'»«„“)\]]+)?$")


def _speech_sentences(speech) -> list:
    """Merge time-ordered ASR segments into complete sentences.

    Segments are pause-delimited and routinely break mid-sentence; readers (and
    the LLM stages) get fluid text by joining segments until one ends a
    sentence. Returns (text, start_s, end_s) per sentence run; a trailing
    fragment with no terminal punctuation still flushes so nothing is lost.
    """
    sentences, texts, start, end = [], [], 0.0, 0.0
    for seg in sorted(speech, key=lambda s: (s.start_s, s.end_s, s.text)):
        text = seg.text.strip()
        if not texts:
            start = seg.start_s
        texts.append(text)
        end = seg.end_s
        if _SENTENCE_END.search(text):
            sentences.append((" ".join(texts), start, end))
            texts = []
    if texts:
        sentences.append((" ".join(texts), start, end))
    return sentences


def render_merged_text(speech, on_screen_text, caption) -> str:
    """Deterministic plain-text rendering of ALL channels, one line each.

    Every extractor MUST build merged_text through this function — sharing the
    renderer means implementations can only disagree about content, never
    format, and determinism is testable in one place. Ordering is fixed:
    speech sentences and video overlays interleaved chronologically — but the
    channels only alternate once a sentence is over, so speech reads fluidly —
    then slides by index, caption last.
    """
    video_texts = [t for t in on_screen_text if t.source.type == "video"]
    slide_texts = [t for t in on_screen_text if t.source.type == "slide"]
    events = [
        (start, 0, f"[SPEECH {start:.1f}-{end:.1f}s] {text}")
        for text, start, end in _speech_sentences(speech)
    ] + [
        (t.source.first_seen_s, 1,
         f"[ON-SCREEN {t.source.first_seen_s:.1f}-{t.source.last_seen_s:.1f}s] {t.text}")
        for t in video_texts
    ]
    lines = [line for _, _, line in sorted(events)]
    for t in sorted(slide_texts, key=lambda t: (t.source.index, t.text)):
        lines.append(f"[SLIDE {t.source.index}] {t.text}")
    if caption:
        lines.append(f"[CAPTION] {caption}")
    return "\n".join(lines)


def build_result(extractor: ExtractorName, speech, on_screen_text, caption, has_speech) -> ExtractionResult:
    """The one way to assemble an ExtractionResult (keeps merged_text canonical)."""
    speech = tuple(speech)
    on_screen_text = tuple(on_screen_text)
    return ExtractionResult(
        speech=speech,
        on_screen_text=on_screen_text,
        caption=caption,
        has_speech=has_speech,
        extractor=extractor,
        merged_text=render_merged_text(speech, on_screen_text, caption),
    )


# -------------------------------------------------------------------- interface
class MediaExtractor(ABC):
    @abstractmethod
    def extract(self, post: MediaPost) -> ExtractionResult:
        """Read all channels of `post` and return the labeled ExtractionResult."""


# ------------------------------------------------------------------------- mock
class MockExtractor(MediaExtractor):
    """Fixture-backed extractor — deterministic, offline, free.

    Looks up each asset by file NAME in mock_data/media_extractions.json and
    merges the canned per-asset channels in asset order. Files are never
    opened; unknown assets simply contribute nothing.
    """

    def __init__(self):
        self._fixtures = json.loads(_FIXTURES_PATH.read_text(encoding="utf-8"))

    def extract(self, post: MediaPost) -> ExtractionResult:
        speech, on_screen, has_speech = [], [], False
        slide_index = 0
        for asset in post.assets:
            fx = self._fixtures.get(os.path.basename(asset.local_path), {})
            if asset.type == "image":
                slide_index += 1
                for text in fx.get("texts", []):
                    on_screen.append(OnScreenText(text, SlideTextSource("slide", slide_index)))
                continue
            # video and audio assets share the speech channel shape
            has_speech = has_speech or fx.get("has_speech", False)
            for seg in fx.get("speech", []):
                speech.append(SpeechSegment(seg["text"], seg["start_s"], seg["end_s"]))
            for ov in fx.get("overlays", []):
                on_screen.append(
                    OnScreenText(
                        ov["text"],
                        VideoTextSource("video", ov["first_seen_s"], ov["last_seen_s"]),
                    )
                )
        return build_result("mock", speech, on_screen, post.caption_text, has_speech)


# --------------------------------------------------------------------- selection
def get_extractor() -> MediaExtractor:
    """Mock by default; `MEDIA_EXTRACTOR=openrouter|local` in .env opts in.

    Asking for a real extractor that cannot run (e.g. openrouter with no key)
    fails loudly instead of silently falling back to mock — an operator who
    opted in must never mistake fixture output for a real extraction.
    """
    load_dotenv()
    # empty value counts as unset → default (so `MEDIA_EXTRACTOR=` is harmless)
    choice = (os.environ.get("MEDIA_EXTRACTOR") or "mock").strip().lower()
    if choice == "mock":
        return MockExtractor()
    if choice == "openrouter":
        from app.extract_openrouter import OpenRouterExtractor

        return OpenRouterExtractor()
    if choice == "local":
        from app.extract_local import LocalExtractor

        return LocalExtractor()
    raise ValueError(
        f"MEDIA_EXTRACTOR={choice!r} is not one of: mock, openrouter, local"
    )
