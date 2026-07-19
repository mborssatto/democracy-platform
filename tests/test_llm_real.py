"""Real-LLM smoke test — hits the live Anthropic API (paid, needs network).

These tests verify that every public function in `app/llm.py` returns its
documented SHAPE when backed by the real model, not the mock. They assert
structure only (types, keys, ranges) — never content quality, because real
model output varies run to run.

Skipped entirely unless ANTHROPIC_API_KEY is available: `app/llm.py` loads
`.env` via python-dotenv at import time and stores the key in
`llm.ANTHROPIC_KEY`, so we key the skip off that attribute (it reflects both
a real environment variable and a `.env` file). Without a key the whole file
is a no-op, keeping `python -m pytest tests/ -q` free and offline.
"""
import pytest

from app import llm

pytestmark = pytest.mark.skipif(
    not llm.ANTHROPIC_KEY,
    reason="ANTHROPIC_API_KEY not set (via env or .env) — skipping paid real-LLM smoke tests",
)

# Small German inputs: a typical Briefwahl (postal-vote) disinformation post.
POST = "Die Briefwahl ist manipuliert! Tausende Stimmzettel wurden gefälscht, das ist Wahlbetrug!"

FACTCHECK = {
    "fact_checker": "Correctiv",
    "verdict": "false",
    "url": "https://correctiv.org/faktencheck/briefwahl",
}

NARRATIVE = {
    "code": "briefwahl_betrug",
    "label_en": "Postal vote fraud",
    "label_de": "Briefwahl-Betrug",
    "description_en": "Claims that postal voting is systematically manipulated.",
}


def test_triage_shape():
    out = llm.triage(POST)
    assert isinstance(out, dict)
    assert isinstance(out["relevant"], bool)
    assert isinstance(out["reason"], str)


def test_translate_shape():
    out = llm.translate(POST)
    assert isinstance(out, str)
    assert out.strip()


def test_classify_shape():
    out = llm.classify(POST)
    assert isinstance(out, dict)
    assert isinstance(out["narrative"], str)
    assert out["narrative"] in set(llm.NARRATIVE_KEYWORDS) | {"none"}
    assert isinstance(out["confidence"], float)
    assert 0.0 <= out["confidence"] <= 1.0


def test_draft_reply_shape_without_factcheck():
    out = llm.draft_reply(POST, None)
    assert isinstance(out, str)
    assert out.strip()


def test_draft_reply_shape_with_factcheck():
    out = llm.draft_reply(POST, FACTCHECK)
    assert isinstance(out, str)
    # Lenient: real models phrase citations differently; just require a
    # non-trivial draft rather than an exact mention of the fact-check.
    assert len(out.strip()) > 20


def test_draft_brief_shape():
    claims = [
        {
            "fact_checker": FACTCHECK["fact_checker"],
            "verdict": FACTCHECK["verdict"],
            "url": FACTCHECK["url"],
            "claim_en": "Thousands of postal ballots were forged.",
        }
    ]
    out = llm.draft_brief(NARRATIVE, claims, [POST])
    assert isinstance(out, dict)
    for key in ("summary_en", "summary_de", "key_facts", "angles", "avoid"):
        assert key in out, f"missing brief key: {key}"
    assert isinstance(out["summary_en"], str)
    assert isinstance(out["summary_de"], str)
    assert isinstance(out["key_facts"], list)
    assert isinstance(out["angles"], list)
    assert isinstance(out["avoid"], list)


def test_substantive_change_shape():
    seed = "Ich verstehe die Sorge, aber der Faktencheck zeigt etwas anderes."
    edited = (
        "Hey, ich habe als Wahlhelferin selbst Briefwahlstimmen ausgezählt — "
        "jede einzelne wird von mehreren Augen geprüft. Der Faktencheck erklärt das gut."
    )
    out = llm.substantive_change(seed, edited)
    assert isinstance(out, dict)
    assert isinstance(out["substantive"], bool)
    assert isinstance(out["reason"], str)
