"""Media extraction tests — all offline.

Covers the seven fixture scenarios from the media-extraction spec
(docs/integrations/media-extraction.md), the deterministic merged_text
rendering, extractor selection, and the TikTok → MediaPost ingestion adapter.
The OpenRouter and Local extractors get their own sections below the mock ones
(added in later TDD cycles).
"""
import json
from pathlib import Path

import httpx
import pytest

from app import connectors, extract
from app import extract_openrouter as orx

ROOT = Path(__file__).resolve().parent.parent
POSTS = {
    p["post_id"]: p
    for p in json.loads(
        (ROOT / "mock_data" / "media_posts.json").read_text(encoding="utf-8")
    )["posts"]
}


def _post(post_id: str) -> extract.MediaPost:
    return extract.media_post_from_dict(POSTS[post_id])


def _run(post_id: str) -> extract.ExtractionResult:
    return extract.MockExtractor().extract(_post(post_id))


# ------------------------------------------------------------- 7 fixture shapes
def test_speech_only_video():
    """(1) Spoken claim → speech segments with timestamps, no overlays."""
    r = _run("tt-speech-only")
    assert r.has_speech is True
    assert [s.text for s in r.speech] == [
        "Die Briefwahl wird massenhaft manipuliert, das ist längst bewiesen.",
        "Teilt das Video, bevor es gelöscht wird.",
    ]
    assert (r.speech[0].start_s, r.speech[0].end_s) == (0.0, 4.2)
    assert r.on_screen_text == ()
    assert "[SPEECH 0.0-4.2s] Die Briefwahl wird massenhaft manipuliert" in r.merged_text
    assert "[CAPTION] Wichtig! Bitte bis zum Ende anschauen." in r.merged_text


def test_mute_video_overlay_text():
    """(2) Mute video: the claim lives in on-screen overlays, speech stays empty."""
    r = _run("tt-mute-overlay")
    assert r.has_speech is False
    assert r.speech == ()
    texts = [t.text for t in r.on_screen_text]
    assert texts == ["BRIEFWAHL = WAHLBETRUG", "DIE MEDIEN SCHWEIGEN"]
    src = r.on_screen_text[0].source
    assert src.type == "video"
    assert (src.first_seen_s, src.last_seen_s) == (0.5, 6.0)
    assert r.caption is None
    assert "[CAPTION]" not in r.merged_text


def test_different_claims_in_speech_and_overlay():
    """(3) Channels are additive: speech and overlay carry DIFFERENT claims."""
    r = _run("tt-speech-vs-overlay")
    assert r.has_speech is True
    assert "digitale Euro" in r.speech[0].text
    assert "BARGELD WIRD 2027 KOMPLETT VERBOTEN" in [t.text for t in r.on_screen_text]
    assert "[SPEECH 0.0-3.8s] Der digitale Euro" in r.merged_text
    assert "[ON-SCREEN 1.0-5.0s] BARGELD WIRD 2027 KOMPLETT VERBOTEN" in r.merged_text


def test_music_only_audio_yields_no_transcript():
    """(4) Music-only sound: speech is empty BECAUSE has_speech is false (VAD
    verdict) — a naive transcript would hallucinate lyrics here."""
    r = _run("tt-music-only")
    assert r.has_speech is False
    assert r.speech == ()
    assert "[SPEECH" not in r.merged_text


def test_slide_post_with_music_track():
    """(5) 4 slides + background music: per-slide indices, no speech."""
    r = _run("tt-slides")
    assert r.has_speech is False
    assert r.speech == ()
    slide_sources = [t.source for t in r.on_screen_text]
    assert all(s.type == "slide" for s in slide_sources)
    assert [s.index for s in slide_sources] == [1, 2, 3, 4]
    assert "[SLIDE 3] Teile das, bevor es gelöscht wird" in r.merged_text
    assert "[CAPTION] Alle Folien lesen und teilen!" in r.merged_text


def test_caption_contradicting_overlay_keeps_both():
    """(6) Caption contradicts the overlay — both appear under their own label."""
    r = _run("tt-caption-contradicts")
    assert "[ON-SCREEN 0.5-6.0s] BRIEFWAHL = WAHLBETRUG" in r.merged_text
    assert "[CAPTION] Die Briefwahl ist sicher — Faktencheck im Kommentar." in r.merged_text


def test_platform_agnostic_instagram_post():
    """(7) Same assets under platform "instagram" extract identically —
    extraction routes on asset types, never on the platform name."""
    tiktok = _run("tt-slides")
    instagram = _run("ig-slides")
    assert instagram.extractor == "mock"
    assert instagram.speech == tiktok.speech
    assert instagram.on_screen_text == tiktok.on_screen_text
    assert instagram.has_speech == tiktok.has_speech
    assert instagram.merged_text == tiktok.merged_text


# ---------------------------------------------------------------- merged_text
def test_merged_text_is_deterministic():
    """Same channels in any input order render to the same document.
    Speech and overlays interleave chronologically (overlay at 1.0s lands
    between the sentence ending at 4.2s and the one starting at 4.6s)."""
    speech = [
        extract.SpeechSegment("Zweiter Satz.", 4.6, 7.1),
        extract.SpeechSegment("Erster Satz.", 0.0, 4.2),
    ]
    on_screen = [
        extract.OnScreenText("Folie zwei", extract.SlideTextSource("slide", 2)),
        extract.OnScreenText("Overlay", extract.VideoTextSource("video", 1.0, 3.0)),
        extract.OnScreenText("Folie eins", extract.SlideTextSource("slide", 1)),
    ]
    a = extract.render_merged_text(speech, on_screen, "Bildunterschrift")
    b = extract.render_merged_text(list(reversed(speech)), list(reversed(on_screen)), "Bildunterschrift")
    assert a == b
    assert a.splitlines() == [
        "[SPEECH 0.0-4.2s] Erster Satz.",
        "[ON-SCREEN 1.0-3.0s] Overlay",
        "[SPEECH 4.6-7.1s] Zweiter Satz.",
        "[SLIDE 1] Folie eins",
        "[SLIDE 2] Folie zwei",
        "[CAPTION] Bildunterschrift",
    ]


def test_merged_text_speech_is_fluid():
    """ASR segments that break mid-sentence merge into one fluid line, and an
    overlay may only intercalate once the sentence is over — never inside it."""
    speech = [
        extract.SpeechSegment("Die Briefwahl wird", 0.0, 1.8),
        extract.SpeechSegment("massenhaft manipuliert,", 1.9, 3.0),
        extract.SpeechSegment("das ist längst bewiesen.", 3.1, 4.2),
        extract.SpeechSegment("Teilt das Video", 4.6, 5.5),  # no final punctuation
    ]
    on_screen = [
        # appears at 2.0s — mid-sentence — so it must wait for the sentence end
        extract.OnScreenText("WACHT AUF", extract.VideoTextSource("video", 2.0, 6.0)),
    ]
    out = extract.render_merged_text(speech, on_screen, None)
    assert out.splitlines() == [
        "[SPEECH 0.0-4.2s] Die Briefwahl wird massenhaft manipuliert, das ist längst bewiesen.",
        "[ON-SCREEN 2.0-6.0s] WACHT AUF",
        "[SPEECH 4.6-5.5s] Teilt das Video",  # trailing fragment still flushes
    ]


def test_mock_extraction_is_repeatable():
    assert _run("tt-slides") == _run("tt-slides")


# ---------------------------------------------------------- extractor selection
def test_default_extractor_is_mock(monkeypatch):
    # empty string = unset; load_dotenv never overrides an existing var, so the
    # developer's real .env (which may opt into openrouter) can't leak in here
    monkeypatch.setenv("MEDIA_EXTRACTOR", "")
    assert isinstance(extract.get_extractor(), extract.MockExtractor)


def test_unknown_extractor_fails_loudly(monkeypatch):
    monkeypatch.setenv("MEDIA_EXTRACTOR", "cloud-magic")
    with pytest.raises(ValueError):
        extract.get_extractor()


# ------------------------------------------------- TikTok → MediaPost adapter
def test_tiktok_ingestion_adapter():
    """The adapter maps an already-acquired TikTok record + files to the
    canonical MediaPost; asset types come from file extensions, in order."""
    video = {"id": "7300000000000000001", "video_description": "Echt jetzt?! #briefwahl"}
    post = connectors.tiktok_media_post(
        video, ["downloads/7300000000000000001.mp4", "downloads/cover.jpg"]
    )
    assert post.platform == "tiktok"
    assert post.post_id == "7300000000000000001"
    assert post.caption_text == "Echt jetzt?! #briefwahl"
    assert [(a.type, a.local_path) for a in post.assets] == [
        ("video", "downloads/7300000000000000001.mp4"),
        ("image", "downloads/cover.jpg"),
    ]


# ------------------------------------------------------- OpenRouter extractor
# Raw media may ONLY go to Gemini pinned to Google Vertex (ZDR, no data
# collection, no fallbacks). The client must make it impossible to build or
# send a request without those pins.

def _or_response(body: dict) -> dict:
    # "provider" mirrors the real API: the name of the provider that actually
    # served the request — _send verifies it against the allowlist post-hoc.
    return {"provider": "Google Vertex",
            "choices": [{"message": {"content": json.dumps(body)}}]}


def test_openrouter_request_carries_all_routing_pins():
    payload = orx.build_request([{"type": "text", "text": "hallo"}])
    p = payload["provider"]
    assert p["order"] == ["google-vertex"]
    assert p["only"] == ["google-vertex"]
    assert p["allow_fallbacks"] is False
    assert p["data_collection"] == "deny"
    assert p["zdr"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("provider"),
        lambda p: p["provider"].pop("allow_fallbacks"),
        lambda p: p["provider"].update(allow_fallbacks=True),
        lambda p: p["provider"].pop("data_collection"),
        lambda p: p["provider"].update(data_collection="allow"),
        lambda p: p["provider"].update(zdr=False),
        lambda p: p["provider"].update(order=["openai"]),
        lambda p: p["provider"].update(only=["google-vertex", "deepinfra"]),
    ],
    ids=[
        "no-provider-block", "fallbacks-missing", "fallbacks-enabled",
        "data-collection-missing", "data-collection-allow", "zdr-off",
        "wrong-provider", "extra-provider",
    ],
)
def test_tampered_openrouter_request_is_refused(mutate):
    payload = orx.build_request([{"type": "text", "text": "hallo"}])
    mutate(payload)
    with pytest.raises(orx.RoutingPinError):
        orx.assert_routing_pins(payload)


def test_openrouter_extractor_requires_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")  # empty = unset, .env can't leak in
    with pytest.raises(RuntimeError):
        orx.OpenRouterExtractor()


def test_get_extractor_openrouter_without_key_fails_loudly(monkeypatch):
    """Opting into the real extractor with no key must never fall back to mock."""
    monkeypatch.setenv("MEDIA_EXTRACTOR", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(RuntimeError):
        extract.get_extractor()


@pytest.fixture
def or_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")


def test_openrouter_slides_batched_in_order_and_pinned(or_key, tmp_path):
    """Image assets go as ordered base64 image parts in ONE request; the
    outgoing body carries the routing pins; the caption is NOT sent."""
    paths = []
    for i in (1, 2):
        p = tmp_path / f"folie_{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff fake-jpeg-bytes")
        paths.append(str(p))
    post = extract.MediaPost(
        "tiktok", "tt-x", "Geheime Bildunterschrift",
        (extract.MediaAsset("image", paths[0]), extract.MediaAsset("image", paths[1])),
    )

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_or_response({
            "has_speech": False,
            "speech": [],
            "on_screen_text": [
                {"text": "Erste Folie", "slide": 1},
                {"text": "Zweite Folie", "slide": 2},
            ],
        }))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r = orx.OpenRouterExtractor().extract(post, client=client)

    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer sk-or-test"
    body = json.loads(requests[0].content)
    orx.assert_routing_pins(body)  # what actually left the process was pinned
    image_parts = [
        part for part in body["messages"][0]["content"] if part["type"] == "image_url"
    ]
    assert len(image_parts) == 2
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "Geheime Bildunterschrift" not in requests[0].content.decode("utf-8")

    assert r.extractor == "openrouter"
    assert r.has_speech is False and r.speech == ()
    assert [(t.text, t.source.index) for t in r.on_screen_text] == [
        ("Erste Folie", 1), ("Zweite Folie", 2),
    ]
    assert r.merged_text.endswith("[CAPTION] Geheime Bildunterschrift")


def test_openrouter_video_as_base64_video_url(or_key, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00 ftypmp42 fake-bytes")
    post = extract.MediaPost(
        "tiktok", "tt-y", None, (extract.MediaAsset("video", str(video)),)
    )

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_or_response({
            "has_speech": True,
            "speech": [{"text": "Hallo zusammen", "start_s": 0.0, "end_s": 1.5}],
            "on_screen_text": [
                {"text": "OVERLAY", "first_seen_s": 1.0, "last_seen_s": 2.0}
            ],
        }))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r = orx.OpenRouterExtractor().extract(post, client=client)

    body = json.loads(requests[0].content)
    orx.assert_routing_pins(body)
    video_parts = [
        part for part in body["messages"][0]["content"] if part["type"] == "video_url"
    ]
    assert len(video_parts) == 1
    assert video_parts[0]["video_url"]["url"].startswith("data:video/mp4;base64,")

    assert r.has_speech is True
    assert r.speech == (extract.SpeechSegment("Hallo zusammen", 0.0, 1.5),)
    assert r.on_screen_text[0].source == extract.VideoTextSource("video", 1.0, 2.0)


def test_openrouter_malformed_reply_quarantines(or_key, tmp_path):
    """Repo convention: on unparseable model output, quarantine — never guess."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    post = extract.MediaPost(
        "tiktok", "tt-z", None, (extract.MediaAsset("video", str(video)),)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"provider": "Google Vertex",
                       "choices": [{"message": {"content": "not json at all"}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(orx.ExtractionError):
            orx.OpenRouterExtractor().extract(post, client=client)


@pytest.mark.parametrize("provider", ["Google", "Google Vertex", "google-vertex"])
def test_openrouter_consistent_served_provider_accepted(or_key, tmp_path, provider):
    """The response's provider field is a coarse display name — a real
    Vertex-served response reports just "Google" (observed 2026-07-21). Names
    consistent with the allowlist must pass."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    post = extract.MediaPost(
        "tiktok", "tt-ok", None, (extract.MediaAsset("video", str(video)),)
    )
    body = {"provider": provider,
            "choices": [{"message": {"content": json.dumps(
                {"speech": [], "on_screen_text": [], "has_speech": False})}}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r = orx.OpenRouterExtractor().extract(post, client=client)
    assert r.has_speech is False


@pytest.mark.parametrize("provider", ["SomeOtherCloud", "Google AI Studio", None])
def test_openrouter_wrong_or_missing_served_provider_raises(or_key, tmp_path, provider):
    """Post-hoc routing verification: the pins only prove what we ASKED for.
    A response served inconsistently with the allowlist — or silent about who
    served it — must raise, not be trusted."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    post = extract.MediaPost(
        "tiktok", "tt-p", None, (extract.MediaAsset("video", str(video)),)
    )
    body = {"choices": [{"message": {"content": json.dumps(
        {"speech": [], "on_screen_text": [], "has_speech": False})}}]}
    if provider is not None:
        body["provider"] = provider

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(orx.RoutingPinError):
            orx.OpenRouterExtractor().extract(post, client=client)


# ------------------------------------------------------------ Local extractor
# Production path, internals stubbed (TODO). What we test NOW is the wiring
# contract: the VAD gate (Whisper never runs on non-speech audio — hard rule,
# Whisper hallucinates fluent text on music) and the always-on visual branch.

def _video_post() -> extract.MediaPost:
    return extract.MediaPost(
        "tiktok", "tt-local", "Lokale Bildunterschrift",
        (extract.MediaAsset("video", "downloads/clip.mp4"),),
    )


def test_local_extractor_selected_via_env(monkeypatch):
    from app.extract_local import LocalExtractor

    monkeypatch.setenv("MEDIA_EXTRACTOR", "local")
    assert isinstance(extract.get_extractor(), LocalExtractor)


def test_local_internals_are_honest_stubs():
    """Until VAD/Whisper/VLM land, extraction must fail loudly, not fake output."""
    from app.extract_local import LocalExtractor

    with pytest.raises(NotImplementedError):
        LocalExtractor().extract(_video_post())


def test_local_vad_gate_blocks_whisper_on_music(monkeypatch):
    """VAD says no speech → Whisper is NEVER called; visual branch still runs."""
    from app.extract_local import LocalExtractor

    transcribe_calls = []
    monkeypatch.setattr(LocalExtractor, "_vad_speech_segments", lambda self, path: [])
    monkeypatch.setattr(
        LocalExtractor,
        "_transcribe_segments",
        lambda self, path, segments: transcribe_calls.append(path) or [],
    )
    monkeypatch.setattr(LocalExtractor, "_sample_frames", lambda self, path: ["frame-0", "frame-1"])
    monkeypatch.setattr(
        LocalExtractor,
        "_vlm_read_frames",
        lambda self, frames: [
            extract.OnScreenText("MEME-TEXT", extract.VideoTextSource("video", 0.0, 2.0))
        ],
    )

    r = LocalExtractor().extract(_video_post())

    assert transcribe_calls == []  # the gate held
    assert r.has_speech is False and r.speech == ()
    assert [t.text for t in r.on_screen_text] == ["MEME-TEXT"]  # visuals always ran
    assert r.extractor == "local"


def test_local_whisper_runs_only_on_vad_segments(monkeypatch):
    """VAD found speech → Whisper gets exactly those segments, nothing more."""
    from app.extract_local import LocalExtractor

    seen = {}
    monkeypatch.setattr(
        LocalExtractor, "_vad_speech_segments", lambda self, path: [(0.4, 3.9)]
    )

    def fake_transcribe(self, path, segments):
        seen["segments"] = segments
        return [extract.SpeechSegment("Hallo Welt", 0.4, 3.9)]

    monkeypatch.setattr(LocalExtractor, "_transcribe_segments", fake_transcribe)
    monkeypatch.setattr(LocalExtractor, "_sample_frames", lambda self, path: [])
    monkeypatch.setattr(LocalExtractor, "_vlm_read_frames", lambda self, frames: [])

    r = LocalExtractor().extract(_video_post())

    assert seen["segments"] == [(0.4, 3.9)]
    assert r.has_speech is True
    assert r.speech == (extract.SpeechSegment("Hallo Welt", 0.4, 3.9),)


# ------------------------------------------------------------------- boundary
def test_extraction_modules_never_touch_reasoning_llm():
    """Canary-style tripwire (hard rule 5): extraction produces merged_text but
    must never hand anything to the reasoning LLM itself — that happens
    downstream, behind the pseudonymization boundary (app/pseudo.py). Raw
    media destinations are limited to local code or the pinned Vertex client."""
    import app.extract
    import app.extract_local
    import app.extract_openrouter

    for module in (app.extract, app.extract_local, app.extract_openrouter):
        assert "llm" not in vars(module), f"{module.__name__} imports app.llm"
