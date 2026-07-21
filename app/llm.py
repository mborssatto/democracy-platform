"""The AI layer — mock by default, real when an API key is present.

This mirrors the plan's ~300-line `llm.py` idea: one thin wrapper, structured
outputs, per-call logging, no frameworks. In mock mode, deterministic heuristics
stand in for the model so the whole prototype runs offline and free.

>>> INTEGRATION POINT (real AI): see docs/integrations/llm.md
    Add ANTHROPIC_API_KEY (or OPENAI_API_KEY) to `.env` and the same functions
    call the real model. Mock and real return identical shapes.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")

# Reasoning model when routed via OpenRouter (one key for everything). Text
# reaching this call is ALWAYS pseudonymized (hard rule 5 — canary-tested), so
# the raw-media Vertex pins from extract_openrouter.py don't apply here; we
# still send data_collection=deny as basic hygiene.
OPENROUTER_REASONING_MODEL = "anthropic/claude-haiku-4.5"

# Deep-analysis model for the INTERPRETIVE stages (narrative evaluation,
# summarize): reading a post's implicit framing — what it portrays as good or
# bad without saying so — needs a stronger model. Chosen empirically
# 2026-07-21: a 3-prompt × 2-model matrix on real content, judged on substance
# (implicit anti-Nazi framing named), neutrality and clarity — Sonnet passed
# all hard rules where Haiku kept slipping on neutrality. Mechanical stages
# (triage, classify, translate) stay on the cheap model above.
OPENROUTER_DEEP_MODEL = "anthropic/claude-sonnet-5"


def mode() -> str:
    if ANTHROPIC_KEY:
        return "real:anthropic"
    if OPENAI_KEY:
        return "real:openai"
    if OPENROUTER_KEY:
        return "real:openrouter"
    return "mock"


# ---------------------------------------------------------------- real backends
def _call_real(system: str, user: str, deep: bool = False) -> str:
    """Minimal real-model call. Returns the model's text reply.

    `deep=True` routes to OPENROUTER_DEEP_MODEL on the OpenRouter backend
    (interpretive stages); the direct Anthropic/OpenAI backends keep their
    single model — deep-vs-cheap tiering exists only where we run for real."""
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
    if OPENAI_KEY:
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
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
        json={
            "model": OPENROUTER_DEEP_MODEL if deep else OPENROUTER_REASONING_MODEL,
            "provider": {"data_collection": "deny"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _real_json(system: str, user: str, deep: bool = False) -> dict:
    """Ask for JSON, parse defensively (prototype-grade)."""
    text = _call_real(system + " Reply with ONLY valid JSON, no prose.", user, deep=deep)
    match = re.search(r"\{.*\}", text, re.S)
    return json.loads(match.group(0)) if match else {}


# ------------------------------------------------------------------ embeddings
# Real embedding vectors for claim matching (pipeline stages 3+4). Separate
# from mode(): embeddings need their own key (OpenAI or Voyage), read at call
# time — like factcheck.api_key() — so tests can control the environment.

EMBED_MODELS = {
    "openai": "text-embedding-3-small",   # ≈ $0.02 / 1M tokens
    "voyage": "voyage-3.5-lite",
}


def embedding_model() -> str | None:
    """The active embedding model, or None when no embedding key is configured
    (mock mode) — callers fall back to word-overlap matching. Prefer OpenAI
    when both keys are present."""
    if os.environ.get("OPENAI_API_KEY"):
        return EMBED_MODELS["openai"]
    if os.environ.get("VOYAGE_API_KEY"):
        return EMBED_MODELS["voyage"]
    return None


def embed_texts(texts: list[str], transport: httpx.BaseTransport | None = None
                ) -> list[list[float]] | None:
    """Embed a batch of texts in ONE API call (cost discipline: never loop
    per-text). Returns one vector per input text, in order, or None when no
    embedding key is configured.

    PSEUDONYMIZATION INVARIANT (hard rule 5): every text passed here must
    already be pseudonymized — post/claim text only. Never send author fields;
    the canary test (tests/test_pipeline.py) enforces the storage side.

    `transport` lets tests inject an httpx.MockTransport — no network needed.
    """
    openai_key = os.environ.get("OPENAI_API_KEY")
    voyage_key = os.environ.get("VOYAGE_API_KEY")
    if not (openai_key or voyage_key):
        return None
    if not texts:
        return []
    if openai_key:  # preferred when both keys are present
        url = "https://api.openai.com/v1/embeddings"
        headers = {"Authorization": f"Bearer {openai_key}"}
        model = EMBED_MODELS["openai"]
    else:
        url = "https://api.voyageai.com/v1/embeddings"
        headers = {"Authorization": f"Bearer {voyage_key}"}
        model = EMBED_MODELS["voyage"]
    with httpx.Client(transport=transport, timeout=60) as client:
        r = client.post(url, headers=headers, json={"model": model, "input": texts})
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data]


# ------------------------------------------------------------------- functions
# Each function: same signature and return shape in mock and real mode.

# Bump this whenever a prompt (or the mock heuristic standing in for it) changes.
# The eval runner (evals/run_evals.py) stamps every score report with it, so we
# always know which prompt version produced which numbers.
PROMPT_VERSION = "v1"

NARRATIVE_KEYWORDS = {
    "briefwahl_betrug": ["briefwahl", "wahlbetrug", "gefälscht", "stimmzettel", "wahlfälschung"],
    "sozialleistungen_migranten": ["migranten", "sozialleistungen", "bürgergeld", "rentner", "asyl"],
    "windkraft_duerre": ["windräder", "windrad", "dürre", "trockenheit", "windkraft"],
    "eu_bargeldverbot": ["bargeld", "bargeldverbot", "eu verbietet", "digitaler euro"],
    "impfpflicht_rueckkehr": ["impfpflicht", "impfzwang", "who", "pandemievertrag"],
    # pro-democracy watchlist entry (category=pro_democracy in narratives.json):
    # matching it means SUPPORT, not misinformation — see PRO_NARRATIVE_CODES.
    "wahlbeteiligung_staerken": ["geht wählen", "geh wählen", "wählen gehen",
                                 "wahlbeteiligung", "demokratie stärken", "faktencheck"],
}


# --------------------------------------------------------- narrative evaluation
# The six evaluation categories (DSA systemic-risk framing + pro-democracy).
# Keys are stable codes stored in enrichments and narratives.category; the
# full plain-language definitions live in the VERSIONED prompt file — the
# prompt text + its sha256 are stored with every result, so any past verdict
# can be traced to the exact wording that produced it (that is the "auditable"
# part). Never edit a prompt file in place: copy to _v2 and bump _PROMPT_PATH.
EVAL_CATEGORIES = (
    "false_information",   # matches a tracked debunked narrative — NO new verdicts
    "illegal_content",     # illegal in DE/EU: incitement, banned symbols, threats
    "hate_speech",         # dehumanizing/degrading speech PRESENT (even if criticized)
    "fundamental_rights",  # attacks dignity, equal treatment, press freedom...
    "civic_discourse",     # delegitimizes elections/participation without evidence
    "vulnerable_groups",   # threatens minors, minorities, or public health
    "pro_democracy",       # the post's overall STANCE supports democratic values
)

# v2 (2026-07-21): + hate_speech category, + presence-vs-stance doctrine (harm
# categories flag what the content CONTAINS — even documented/criticized
# footage; pro_democracy is the post's stance and may co-apply).
# v3 (2026-07-21): + "understand before judging" two-step — read the post's
# implicit message (what its framing portrays as good/bad, e.g. commemorating
# anti-Nazi resistance = portraying the regime as wrong) before applying
# categories. Winner of a judged 3-prompt × 2-model matrix on real content.
_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "narrative_eval_v3.md"
_prompt_cache: dict = {}


def _narrative_eval_prompt() -> tuple[str, str, str]:
    """(version, text, sha256) of the current evaluation prompt, cached."""
    if not _prompt_cache:
        text = _PROMPT_PATH.read_text(encoding="utf-8")
        _prompt_cache.update(
            version=_PROMPT_PATH.stem,
            text=text,
            sha=hashlib.sha256(_PROMPT_PATH.read_bytes()).hexdigest(),
        )
    return _prompt_cache["version"], _prompt_cache["text"], _prompt_cache["sha"]


def narrative_eval_prompt_sha() -> str:
    return _narrative_eval_prompt()[2]


# Narratives whose match means SUPPORT, not misinformation (mirrors
# narratives.json category=pro_democracy — mock-mode only; the DB is the
# source of truth for real classification).
PRO_NARRATIVE_CODES = {"wahlbeteiligung_staerken"}

_EVAL_MOCK_KEYWORDS = {
    "illegal_content": ["gewalt gegen", "vernichten", "aufhängen", "waffe"],
    "hate_speech": ["schwein", "abschaum", "ungeziefer", "volksverräter", "ratten"],
    "fundamental_rights": ["lügenpresse", "zensur", "systemmedien", "menschenwürde"],
    "civic_discourse": ["wahlbetrug", "briefwahl", "stimmzettel", "wahl manipuliert",
                        "wählt nicht", "wahlen sind sinnlos"],
    "vulnerable_groups": ["impfpflicht", "impfzwang", "migranten", "asyl", "kinder"],
    "pro_democracy": ["geht wählen", "geh wählen", "wählen gehen", "wahlbeteiligung",
                      "faktencheck", "beteiligung", "demokratie stärken"],
}


def narrative_eval(text: str) -> dict:
    """Stage 2b: which of the six categories apply, with one-line rationales.

    Every result carries prompt_version + prompt_sha256. Real mode sends the
    versioned prompt file verbatim as the system prompt; invalid model output
    is QUARANTINED (categories empty), never guessed (repo convention)."""
    version, prompt_text, sha = _narrative_eval_prompt()
    meta = {"prompt_version": version, "prompt_sha256": sha}

    if mode() != "mock":
        raw = _real_json(prompt_text, text, deep=True)
        categories = {}
        for code in EVAL_CATEGORIES:
            cat = raw.get(code)
            if not isinstance(cat, dict) or not isinstance(cat.get("applies"), bool) \
                    or not isinstance(cat.get("rationale"), str):
                return meta | {"quarantined": True, "categories": {}}
            categories[code] = {"applies": cat["applies"], "rationale": cat["rationale"]}
        return meta | {"categories": categories}

    # Mock: deterministic keyword heuristics, same shape as the real model.
    t = text.lower()
    matched = classify(text)["narrative"]
    tracked_false = matched != "none" and matched not in PRO_NARRATIVE_CODES
    categories = {
        "false_information": {
            "applies": tracked_false,
            "rationale": (f"matches tracked false narrative '{matched}' "
                          "(no new truth verdict generated)" if tracked_false
                          else "no tracked false narrative matched"),
        }
    }
    for code in ("illegal_content", "hate_speech", "fundamental_rights",
                 "civic_discourse", "vulnerable_groups"):
        hits = [k for k in _EVAL_MOCK_KEYWORDS[code] if k in t]
        categories[code] = {
            "applies": bool(hits),
            "rationale": (f"keyword signal: {', '.join(hits)}" if hits
                          else "no signal for this category"),
        }
    # Presence vs. stance (prompt v2): harm PRESENCE no longer blocks the
    # pro_democracy stance — only endorsing a tracked false narrative does.
    # (Keyword mocks can't judge stance; the real model reads the doctrine.)
    pro_hits = [k for k in _EVAL_MOCK_KEYWORDS["pro_democracy"] if k in t]
    categories["pro_democracy"] = {
        "applies": bool(pro_hits) and not tracked_false,
        "rationale": (f"supportive civic signal: {', '.join(pro_hits)}"
                      if pro_hits and not tracked_false
                      else "matches a tracked false narrative" if pro_hits
                      else "no pro-democracy signal"),
    }
    return meta | {"categories": categories}


# Mock-mode topic labels per tracked narrative (real mode asks the model).
_TOPIC_BY_NARRATIVE = {
    "briefwahl_betrug": "elections",
    "sozialleistungen_migranten": "migration",
    "windkraft_duerre": "climate & energy",
    "eu_bargeldverbot": "eu policy",
    "impfpflicht_rueckkehr": "public health",
    "wahlbeteiligung_staerken": "civic participation",
}


def summarize(text: str) -> dict:
    """Content summary + one general topic for the ingest cards.

    Runs for EVERY media-extracted item, including triaged-out ones — the
    submitter always sees what the system read. Invalid real-model output is
    quarantined (nulls), never guessed (repo convention)."""
    if mode() != "mock":
        out = _real_json(
            "You summarize a social media post for a monitoring dashboard. The text "
            "may carry labeled channels ([SPEECH]/[ON-SCREEN]/[SLIDE]/[CAPTION]). "
            "First work out what the post is ABOUT — including the implicit message "
            "its framing conveys (what it treats as good or bad without saying so). "
            'Output JSON {"title": str, "summary": str, "topic": str} — title: a '
            "4-8 word plain headline naming what the post is about (sentence case, "
            "no hashtags, no trailing period). summary: 2 plain-English sentences: "
            "what the post shows, then what its framing conveys when that adds "
            "meaning (e.g. reporting that commemorates resistance against a "
            "dictatorship implicitly portrays that regime as wrong). Attribute the "
            "message to the post — never editorialize in your own voice. "
            "topic: the ONE general theme in 1-3 lowercase English words "
            "(e.g. 'elections', 'immigration', 'historical memory', 'public health').",
            text, deep=True,
        )
        title, summary, topic = out.get("title"), out.get("summary"), out.get("topic")
        if not isinstance(title, str) or not title.strip() \
                or not isinstance(summary, str) or not summary.strip() \
                or not isinstance(topic, str) or not topic.strip():
            return {"quarantined": True, "title": None, "summary": None, "topic": None}
        # Models sometimes return a comma list despite "ONE theme" — keep the
        # primary one so the topic pill stays a label, not a paragraph.
        topic = topic.split(",")[0].strip().lower()
        return {"title": title.strip().rstrip("."), "summary": summary.strip(), "topic": topic}

    # Mock: first channel-stripped line as summary, topic via the keyword map,
    # title = the summary's first words (deterministic).
    plain = re.sub(r"\[(SPEECH|ON-SCREEN|SLIDE|CAPTION)[^\]]*\]\s*", "", text).strip()
    first = plain.splitlines()[0].strip() if plain else text.strip()
    summary = first if len(first) <= 160 else first[:157] + "…"
    words = summary.split()
    title = " ".join(words[:7]) + ("…" if len(words) > 7 else "")
    topic = _TOPIC_BY_NARRATIVE.get(classify(text)["narrative"], "general")
    return {"title": title, "summary": summary, "topic": topic}


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


def classify(text: str, codes: list | None = None) -> dict:
    """Stage 2: which tracked narrative does this post belong to?
    `codes` (from the narratives table) lets newly added narratives take part
    in real-mode classification; mock mode scans its static keyword map."""
    if mode() != "mock":
        return _real_json(
            "Classify the post into one of these narrative codes or 'none': "
            + ", ".join(codes or NARRATIVE_KEYWORDS) +
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
            "You draft a short English reply (max 70 words) to a German social media post spreading "
            f"disinformation. Strategy: {strategy} — address the person respectfully, name shared "
            "values, correct gently. Base facts ONLY on the provided fact-check. Never invent facts. "
            "Never mention political parties or voting. This is a STARTING POINT a human will rewrite "
            "(and translate to German before posting).",
            f"Post: {post_text}\n\n{fc}",
        )
    fc_line = (
        f"The fact-check by {factcheck['fact_checker']} comes to a different conclusion: {factcheck['url']}"
        if factcheck else "It's worth taking a close look at the source on this one."
    )
    return (
        "I understand why this topic feels worrying – it does for a lot of people. "
        f"{fc_line} Maybe have a look? "
        "Especially on topics this important, I think it helps if we stick to the facts."
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
