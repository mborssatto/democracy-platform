"""OpenRouter reasoning backend for app/llm.py — offline tests.

Reasoning (triage, classify, narrative eval, drafts) may run through
OpenRouter when only OPENROUTER_API_KEY is configured. Text sent there is
ALWAYS pseudonymized (hard rule 5 + the canary test); these tests prove
provider precedence, request shape (incl. data_collection=deny hygiene), and
that the test suite as a whole stays in mock mode despite the developer's
real .env carrying an OpenRouter key.
"""
import json

import pytest

from app import llm


def test_suite_default_is_mock():
    """The conftest guard nulls the ambient OpenRouter key: a dev .env with a
    real key must never flip the offline suite into paid API calls."""
    assert llm.mode() == "mock"


def test_mode_openrouter_when_only_that_key(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", None)
    monkeypatch.setattr(llm, "OPENAI_KEY", None)
    monkeypatch.setattr(llm, "OPENROUTER_KEY", "sk-or-test")
    assert llm.mode() == "real:openrouter"


def test_direct_anthropic_takes_precedence(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", "sk-ant-test")
    monkeypatch.setattr(llm, "OPENAI_KEY", None)
    monkeypatch.setattr(llm, "OPENROUTER_KEY", "sk-or-test")
    assert llm.mode() == "real:anthropic"


def test_openrouter_call_shape(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", None)
    monkeypatch.setattr(llm, "OPENAI_KEY", None)
    monkeypatch.setattr(llm, "OPENROUTER_KEY", "sk-or-test")

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "model reply"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, body=json)
        return FakeResponse()

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    reply = llm._call_real("system prompt", "user prompt")

    assert reply == "model reply"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-test"
    body = captured["body"]
    assert body["model"] == llm.OPENROUTER_REASONING_MODEL
    assert body["provider"] == {"data_collection": "deny"}
    assert body["messages"][0] == {"role": "system", "content": "system prompt"}
    assert body["messages"][1] == {"role": "user", "content": "user prompt"}

    # deep=True routes interpretive stages to the deep-analysis model
    llm._call_real("system prompt", "user prompt", deep=True)
    assert captured["body"]["model"] == llm.OPENROUTER_DEEP_MODEL


def test_reply_banned_opener_guard():
    """The code-level guard catches variants opening with stock empathy
    phrases (the prompt bans them, but models occasionally slip)."""
    from app.llm import _variants_with_banned_opener

    bad = _variants_with_banned_opener(
        "Warm: I understand why this worries you.\n"
        "Factual: The fact-check says otherwise: https://x\n"
        "Wry: I get that it spreads fast."
    )
    assert bad == ["Warm", "Wry"]
    assert _variants_with_banned_opener(
        "Warm: A lot of people share this worry.\n"
        "Factual: CORRECTIV rated it false.\n"
        "Wry: Big claims travel fast."
    ) == []
