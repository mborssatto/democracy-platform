"""Auditable narrative evaluation (stage 2b) + narrative categories.

The evaluation judges each item against six categories — false/misleading
(tracked-narrative match only, NO new truth verdicts: hard rule 3), illegal
content, fundamental rights, civic discourse, minors/minorities/public
health, and pro-democracy — using a VERSIONED prompt file whose sha256 is
stored with every result (that is what makes it auditable).
"""
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, llm, main, media_ingest, pipeline
from conftest import login_headers

PROMPT_PATH = Path(__file__).resolve().parent.parent / "app" / "prompts" / "narrative_eval_v3.md"

HARM_TEXT = "Die Briefwahl ist manipuliert! Tausende Stimmzettel gefälscht — Wahlbetrug!"
PRO_TEXT = "Geht wählen! Unsere Demokratie lebt von Beteiligung. Lest den Faktencheck dazu."


@pytest.fixture()
def client(fresh_db):
    con = db.connect()
    pipeline.seed_reference_data(con)
    con.close()
    with TestClient(main.app, raise_server_exceptions=False) as c:
        c.headers.update(login_headers(c, "staff"))
        yield c


# ------------------------------------------------------------------ the prompt
def test_prompt_file_names_every_category_and_forbids_verdicts():
    text = PROMPT_PATH.read_text(encoding="utf-8")
    for code in llm.EVAL_CATEGORIES:
        assert code in text, f"category '{code}' missing from prompt"
    # hard rule 3 must be encoded in the prompt itself
    assert "fact-check verdict" in text.lower()


def test_prompt_is_party_neutral():
    text = PROMPT_PATH.read_text(encoding="utf-8")
    for party in ["AfD", "CDU", "SPD", "Grüne", "FDP", "Linke", "CSU", "BSW"]:
        assert party not in text, f"party reference '{party}' in evaluation prompt"


def test_prompt_hash_matches_file():
    expected = hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()
    assert llm.narrative_eval_prompt_sha() == expected


# ------------------------------------------------------------------- mock eval
def test_mock_eval_shape_and_audit_fields():
    out = llm.narrative_eval(HARM_TEXT)
    assert out["prompt_version"] == "narrative_eval_v3"
    assert out["prompt_sha256"] == llm.narrative_eval_prompt_sha()
    assert set(out["categories"]) == set(llm.EVAL_CATEGORIES)
    for cat in out["categories"].values():
        assert isinstance(cat["applies"], bool)
        assert isinstance(cat["rationale"], str) and cat["rationale"]
    assert out == llm.narrative_eval(HARM_TEXT)  # deterministic


def test_mock_eval_flags_harm_not_pro_democracy():
    cats = llm.narrative_eval(HARM_TEXT)["categories"]
    assert cats["false_information"]["applies"] is True
    assert "briefwahl_betrug" in cats["false_information"]["rationale"]
    assert cats["civic_discourse"]["applies"] is True
    assert cats["pro_democracy"]["applies"] is False


def test_mock_eval_flags_pro_democracy():
    cats = llm.narrative_eval(PRO_TEXT)["categories"]
    assert cats["pro_democracy"]["applies"] is True
    assert cats["false_information"]["applies"] is False
    assert cats["civic_discourse"]["applies"] is False


def test_mock_eval_hate_speech_presence_and_pro_stance_coexist():
    """Prompt v2 doctrine: harm categories flag PRESENCE (even in documented
    footage); pro_democracy is the post's STANCE — they may co-apply. Example:
    reporting that criticizes a hateful confrontation it shows."""
    text = ('[SPEECH 22.4-25.1s] Sie waren letztes Mal schon so ein Schwein. '
            'Lassen Sie das.\n[CAPTION] Faktencheck: was beim Gedenken geschah — '
            'Demokratie stärken heißt hinschauen.')
    cats = llm.narrative_eval(text)["categories"]
    assert cats["hate_speech"]["applies"] is True
    assert cats["pro_democracy"]["applies"] is True


# ------------------------------------------------------------------- real eval
def test_real_eval_sends_versioned_prompt(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", "sk-ant-test")
    captured = {}

    def fake_real_json(system, user, deep=False):
        captured.update(system=system, user=user, deep=deep)
        return {code: {"applies": code == "civic_discourse", "rationale": "test"}
                for code in llm.EVAL_CATEGORIES}

    monkeypatch.setattr(llm, "_real_json", fake_real_json)
    out = llm.narrative_eval(HARM_TEXT)

    assert captured["system"] == PROMPT_PATH.read_text(encoding="utf-8")
    assert captured["user"] == HARM_TEXT
    assert captured["deep"] is True  # interpretive stage → deep-analysis model
    assert out["categories"]["civic_discourse"]["applies"] is True
    assert out["prompt_sha256"] == llm.narrative_eval_prompt_sha()
    assert "quarantined" not in out


def test_real_eval_quarantines_malformed_output(monkeypatch):
    """Repo convention: validate JSON, quarantine on failure — never guess."""
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", "sk-ant-test")
    monkeypatch.setattr(llm, "_real_json", lambda system, user, deep=False: {"nonsense": True})
    out = llm.narrative_eval(HARM_TEXT)
    assert out["quarantined"] is True
    assert out["categories"] == {}
    assert out["prompt_version"] == "narrative_eval_v3"


# ------------------------------------------------- pipeline stage + categories
def _ingest_one(con, text):
    item_id = media_ingest.insert_content_item(con, {
        "platform": "tiktok", "platform_item_id": "eval-test-1",
        "real_author_id": "someuser", "followers_band": "10k-100k",
        "text": text, "text_en": None, "url": "https://example.org/x",
        "posted_at": db.now(), "views": 9000, "views_prev": 4000,
        "likes": 5, "comments": 2, "shares": 1,
    })
    return con.execute("SELECT * FROM content_items WHERE id=?", (item_id,)).fetchone()


def test_pipeline_stores_narrative_eval_enrichment(fresh_db):
    con = db.connect()
    try:
        pipeline.seed_reference_data(con)
        item = _ingest_one(con, HARM_TEXT)
        claims = con.execute("SELECT * FROM claims").fetchall()
        log = pipeline.enrich_item(con, item, claims)
        con.commit()
        assert log["narrative_evaluated"] == 1
        row = con.execute(
            "SELECT * FROM enrichments WHERE item_id=? AND stage='narrative_eval'",
            (item["id"],)).fetchone()
        assert row is not None
        assert "narrative_eval_v3" in row["output_json"]
    finally:
        con.close()


def test_narrative_categories_seeded(fresh_db):
    con = db.connect()
    try:
        pipeline.seed_reference_data(con)
        rows = {r["code"]: r["category"] for r in
                con.execute("SELECT code, category FROM narratives").fetchall()}
    finally:
        con.close()
    assert rows["briefwahl_betrug"] == "civic_discourse"
    assert rows["impfpflicht_rueckkehr"] == "vulnerable_groups"
    assert rows["wahlbeteiligung_staerken"] == "pro_democracy"


# -------------------------------------------------------------- API: narratives
def test_add_narrative_endpoint_and_radar_category(client):
    r = client.post("/api/narratives", json={
        "code": "medien_luegen",
        "label_de": "Die Medien lügen alle",
        "label_en": "All media are lying",
        "description_en": "Blanket press-delegitimization narrative.",
        "category": "fundamental_rights",
    })
    assert r.status_code == 200

    dup = client.post("/api/narratives", json={
        "code": "medien_luegen", "label_de": "x", "label_en": "x",
        "description_en": "x", "category": "fundamental_rights"})
    assert dup.status_code == 409

    bad = client.post("/api/narratives", json={
        "code": "x_y", "label_de": "x", "label_en": "x",
        "description_en": "x", "category": "not_a_category"})
    assert bad.status_code == 422

    radar = client.get("/api/radar").json()
    added = next(n for n in radar if n["code"] == "medien_luegen")
    assert added["category"] == "fundamental_rights"
    assert added["post_count"] == 0  # empty narratives now visible (addable UX)
    pro = next(n for n in radar if n["code"] == "wahlbeteiligung_staerken")
    assert pro["category"] == "pro_democracy"
