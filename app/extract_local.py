"""Local media extraction — the production path. Wiring real, internals TODO.

Everything runs on our own hardware, so raw media never leaves the process at
all (the strongest privacy posture — see docs/integrations/media-extraction.md).
The orchestration in `extract()` is the contract and is tested today; each
`_underscore` internal raises NotImplementedError until the real dependency
lands (never fake output silently).

Two branches, deliberately independent:

- AUDIO: Silero VAD first; faster-whisper runs ONLY on segments VAD marked as
  speech. This gate is a HARD RULE, not an optimization — Whisper hallucinates
  fluent text on music-only audio, which would poison claim extraction
  downstream. Slideshow background audio is usually trending music (VAD
  correctly yields nothing) but sometimes carries voiceover, so audio assets
  go through the same gate as video soundtracks.
- VISUAL: runs ALWAYS, speech or not. Video → sample frames at ~1 fps, dedupe
  near-identical frames; slide images → every slide as-is. Both go to a local
  VLM (Qwen2.5-VL class) for OCR/description: overlay text, burned-in
  captions, sticker text, meme text. Open-weight VLMs stay LOCAL-ONLY —
  never routed through OpenRouter, whose provider pool for them is
  uncontrolled (see app/extract_openrouter.py).
"""
from typing import List, Tuple

from app import extract as ex


class LocalExtractor(ex.MediaExtractor):
    def extract(self, post: ex.MediaPost) -> ex.ExtractionResult:
        speech: list = []
        on_screen: list = []
        has_speech = False

        # --- audio branch: VAD gate, then (and only then) Whisper ----------
        for asset in post.assets:
            if asset.type not in ("video", "audio"):
                continue
            segments = self._vad_speech_segments(asset.local_path)
            if not segments:
                continue  # THE GATE: Whisper never sees non-speech audio
            has_speech = True
            speech += self._transcribe_segments(asset.local_path, segments)

        # --- visual branch: runs ALWAYS ------------------------------------
        slide_index = 0
        for asset in post.assets:
            if asset.type == "video":
                frames = self._sample_frames(asset.local_path)
                on_screen += self._vlm_read_frames(frames)
            elif asset.type == "image":
                slide_index += 1
                on_screen += self._vlm_read_slide(asset.local_path, slide_index)

        return ex.build_result("local", speech, on_screen, post.caption_text, has_speech)

    # ----------------------------------------------------------- TODO internals
    def _vad_speech_segments(self, path: str) -> List[Tuple[float, float]]:
        """TODO: Silero VAD over the audio track → [(start_s, end_s), ...]
        for speech only. Empty list = no speech (the has_speech verdict)."""
        raise NotImplementedError("Silero VAD not wired yet — see module docstring")

    def _transcribe_segments(self, path: str, segments) -> List[ex.SpeechSegment]:
        """TODO: faster-whisper on exactly `segments`, never the full track."""
        raise NotImplementedError("faster-whisper not wired yet")

    def _sample_frames(self, path: str) -> list:
        """TODO: decode video at ~1 fps, drop near-identical frames
        (perceptual hash), keep (timestamp, frame) pairs."""
        raise NotImplementedError("frame sampling not wired yet")

    def _vlm_read_frames(self, frames) -> List[ex.OnScreenText]:
        """TODO: local VLM (Qwen2.5-VL class) OCR over sampled frames,
        merging repeated text into first_seen/last_seen spans."""
        raise NotImplementedError("local VLM not wired yet")

    def _vlm_read_slide(self, path: str, index: int) -> List[ex.OnScreenText]:
        """TODO: local VLM OCR of one slide image → SlideTextSource(index)."""
        raise NotImplementedError("local VLM not wired yet")
