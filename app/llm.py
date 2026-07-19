"""The AI layer — mock by default, real when an API key is present.

This mirrors the plan's ~300-line `llm.py` idea: one thin wrapper, structured
outputs, per-call logging, no frameworks. In mock mode, deterministic heuristics
stand in for the model so the whole prototype runs offline and free.

>>> INTEGRATION POINT (real AI): see docs/integrations/llm.md
    Add ANTHROPIC_API_KEY (or OPENAI_API_KEY) to `.env` and the same functions
    call the real model. Mock and real return identical shapes.
"""
import json
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")


def mode() -> str:
    if ANTHROPIC_KEY:
        return "real:anthropic"
    if OPENAI_KEY:
        return "real:openai"
    return "mock"


# ---------------------------------------------------------------- real backends
def _call_real(system: str, user: str) -> str:
    """Minimal real-model call. Returns the model's text reply."""
    if ANTHROPIC_KEY:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 800,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _real_json(system: str, user: str) -> dict:
    """Ask for JSON, parse defensively (prototype-grade)."""
    text = _call_real(system + " Reply with ONLY valid JSON, no prose.", user)
    match = re.search(r"\{.*\}", text, re.S)
    return json.loads(match.group(0)) if match else {}


# ------------------------------------------------------------------- functions
# Each function: same signature and return shape in mock and real mode.

NARRATIVE_KEYWORDS = {
    "briefwahl_betrug": ["briefwahl", "wahlbetrug", "gefälscht", "stimmzettel", "wahlfälschung"],
    "sozialleistungen_migranten": ["migranten", "sozialleistungen", "bürgergeld", "rentner", "asyl"],
    "windkraft_duerre": ["windräder", "windrad", "dürre", "trockenheit", "windkraft"],
    "eu_bargeldverbot": ["bargeld", "bargeldverbot", "eu verbietet", "digitaler euro"],
    "impfpflicht_rueckkehr": ["impfpflicht", "impfzwang", "who", "pandemievertrag"],
}


def triage(text: str) -> dict:
    """Stage 1: is this relevant to German political discourse at all?"""
    if mode() != "mock":
        return _real_json(
            "You triage German social posts for a counter-disinformation monitor. "
            'Output JSON {"relevant": bool, "reason": str}.',
            text,
        )
    t = text.lower()
    hit = any(k in t for kws in NARRATIVE_KEYWORDS.values() for k in kws)
    political = hit or any(w in t for w in ["politik", "regierung", "demokratie", "wahl"])
    return {"relevant": political, "reason": "keyword match" if political else "no political signal"}


def translate(text: str, precomputed: str | None = None) -> str:
    """English translation. Mock mode uses the fixture's precomputed translation."""
    if mode() != "mock":
        return _call_real("Translate this German social media post to natural English. Reply with only the translation.", text)
    return precomputed or f"[EN] {text}"


def classify(text: str) -> dict:
    """Stage 2: which tracked narrative does this post belong to?"""
    if mode() != "mock":
        return _real_json(
            "Classify the post into one of these narrative codes or 'none': "
            + ", ".join(NARRATIVE_KEYWORDS) +
            '. Output JSON {"narrative": str, "confidence": float}.',
            text,
        )
    t = text.lower()
    best, hits = "none", 0
    for code, kws in NARRATIVE_KEYWORDS.items():
        n = sum(1 for k in kws if k in t)
        if n > hits:
            best, hits = code, n
    return {"narrative": best, "confidence": min(0.95, 0.5 + 0.15 * hits)}


def draft_reply(post_text: str, factcheck: dict | None, strategy: str = "empathy") -> str:
    """Democracy Bot seed draft. Grounded ONLY on the matched fact-check."""
    if mode() != "mock":
        fc = (
            f"Fact-check by {factcheck['fact_checker']}: verdict '{factcheck['verdict']}' — {factcheck['url']}"
            if factcheck else "No fact-check matched; stay general, cite nothing."
        )
        return _call_real(
            "You draft a short German reply (max 70 words) to a social media post spreading "
            f"disinformation. Strategy: {strategy} — address the person respectfully, name shared "
            "values, correct gently. Base facts ONLY on the provided fact-check. Never invent facts. "
            "Never mention political parties or voting. This is a STARTING POINT a human will rewrite.",
            f"Post: {post_text}\n\n{fc}",
        )
    fc_line = (
        f"Der Faktencheck von {factcheck['fact_checker']} kommt zu einem anderen Ergebnis: {factcheck['url']}"
        if factcheck else "Es lohnt sich, die Quelle dazu genau anzuschauen."
    )
    return (
        "Ich verstehe, dass das Thema Sorgen macht – das geht vielen so. "
        f"{fc_line} Vielleicht magst du da mal reinschauen? "
        "Ich finde, gerade bei so wichtigen Themen hilft es, wenn wir bei den Fakten bleiben."
    )


def draft_brief(narrative: dict, claims: list[dict], example_posts: list[str]) -> dict:
    """Creator Studio brief for a spiking narrative."""
    if mode() != "mock":
        return _real_json(
            "Create a creator brief as JSON with keys: summary_en, summary_de, "
            "key_facts (list, each citing one provided fact-check), angles (list of 3), "
            "avoid (list of 3). Use ONLY the provided fact-checks as factual sources.",
            json.dumps({"narrative": narrative, "factchecks": claims, "examples": example_posts}, ensure_ascii=False),
        )
    facts = [
        f"{c['fact_checker']} rated this '{c['verdict']}': {c['claim_en']} ({c['url']})"
        for c in claims
    ] or ["No published fact-check yet — treat all claims as unverified, link only official statistics."]
    return {
        "summary_en": f"The narrative '{narrative['label_en']}' is gaining traction. {narrative['description_en']}",
        "summary_de": f"Das Narrativ „{narrative['label_de']}“ verbreitet sich aktuell stärker.",
        "key_facts": facts,
        "angles": [
            "Empathy first: acknowledge the underlying worry before correcting the claim.",
            "Show, don't lecture: one concrete number or example beats a wall of statistics.",
            "Speak to the silent audience — the goal is the thread's climate, not the loudest voice.",
        ],
        "avoid": [
            "Repeating the false claim in your headline (it amplifies it).",
            "Mockery or 'gotcha' tone — evidence says it backfires.",
            "Any mention of parties or voting (strict neutrality rule).",
        ],
    }


def substantive_change(seed: str, current: str) -> dict:
    """Primary edit gate: did the human meaningfully rework the text?"""
    if mode() != "mock":
        return _real_json(
            "Compare an AI seed draft and a human edit. Did the human make SUBSTANTIVE "
            "changes (own words/framing), not just synonym swaps? "
            'Output JSON {"substantive": bool, "reason": str}.',
            f"SEED:\n{seed}\n\nEDITED:\n{current}",
        )
    seed_words = set(re.findall(r"\w+", seed.lower()))
    cur_words = set(re.findall(r"\w+", current.lower()))
    new_words = cur_words - seed_words
    substantive = len(new_words) >= 5 and current.strip() != seed.strip()
    return {
        "substantive": substantive,
        "reason": f"{len(new_words)} genuinely new words" if substantive
        else "Too close to the AI seed — add your own words and perspective.",
    }
