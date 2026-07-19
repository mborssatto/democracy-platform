"""M4.3 — embedding-based claim matching, tested with a fake transport (no network).

The fake embedder maps texts onto axis-aligned unit vectors keyed by topic
keyword, so cosine similarity is exactly 1.0 for the matching post/claim pair
and 0.0 for everything else — fully deterministic, no API key, no network.
"""
import json

import httpx
import pytest

from app import db, llm, pipeline

# --------------------------------------------------------------- fake backend
def _vector_for(text: str) -> list[float]:
    t = text.lower()
    if "briefwahl" in t:
        return [1.0, 0.0, 0.0]
    if "windräder" in t or "windrad" in t:
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


class FakeEmbedAPI:
    """httpx.MockTransport handler that mimics the OpenAI/Voyage response shape
    and records every request (for call/batch-count assertions)."""

    def __init__(self):
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append({"url": str(request.url), "input": payload["input"]})
        data = [{"index": i, "embedding": _vector_for(t)}
                for i, t in enumerate(payload["input"])]
        return httpx.Response(200, json={"data": data, "model": payload["model"]})


@pytest.fixture()
def fake_api():
    return FakeEmbedAPI()


@pytest.fixture()
def con(fresh_db):
    c = db.connect()
    c.execute(db.sql_insert_ignore(
        "INTO claims (id, claim_de, claim_en, verdict, fact_checker, url, narrative_code, source) "
        "VALUES (1, 'Die Briefwahl wird massenhaft gefälscht', 'Postal votes are forged', "
        "'false', 'Correctiv', 'https://example.org/fc1', 'briefwahl_betrug', 'mock')"))
    c.execute(db.sql_insert_ignore(
        "INTO claims (id, claim_de, claim_en, verdict, fact_checker, url, narrative_code, source) "
        "VALUES (2, 'Windräder verursachen Dürre', 'Wind turbines cause drought', "
        "'false', 'dpa', 'https://example.org/fc2', 'windkraft_duerre', 'mock')"))
    c.commit()
    yield c
    c.close()


def _claims(con):
    return con.execute("SELECT * FROM claims ORDER BY id").fetchall()


# -------------------------------------------------------------------- cosine
def test_cosine():
    assert pipeline.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert pipeline.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert pipeline.cosine([1.0, 0.0], [3.0, 0.0]) == pytest.approx(1.0)  # scale-invariant
    assert pipeline.cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert pipeline.cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero vector: defined as 0


# ------------------------------------------------------------ caching (a)
def test_claim_vectors_cached_and_not_reembedded(con, fake_api, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    transport = httpx.MockTransport(fake_api)

    vectors = pipeline.ensure_claim_embeddings(con, _claims(con), transport=transport)
    assert vectors == {1: [1.0, 0.0, 0.0], 2: [0.0, 1.0, 0.0]}
    assert len(fake_api.requests) == 1                       # ONE batch call
    assert len(fake_api.requests[0]["input"]) == 2
    assert "api.openai.com" in fake_api.requests[0]["url"]

    rows = con.execute("SELECT * FROM claim_embeddings ORDER BY claim_id").fetchall()
    assert [r["claim_id"] for r in rows] == [1, 2]
    assert all(r["model"] == "text-embedding-3-small" for r in rows)

    # Re-run: everything cached, the API must not be called again.
    vectors2 = pipeline.ensure_claim_embeddings(con, _claims(con), transport=transport)
    assert vectors2 == vectors
    assert len(fake_api.requests) == 1

    # A new claim triggers exactly one more call, embedding ONLY the new one.
    con.execute(db.sql_insert_ignore(
        "INTO claims (id, claim_de, narrative_code, source) "
        "VALUES (3, 'Die EU verbietet Bargeld', 'eu_bargeldverbot', 'mock')"))
    con.commit()
    vectors3 = pipeline.ensure_claim_embeddings(con, _claims(con), transport=transport)
    assert len(fake_api.requests) == 2
    assert fake_api.requests[1]["input"] == ["Die EU verbietet Bargeld"]
    assert vectors3[3] == [0.0, 0.0, 1.0]


def test_voyage_used_when_only_voyage_key(con, fake_api, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    vecs = llm.embed_texts(["Briefwahl"], transport=httpx.MockTransport(fake_api))
    assert vecs == [[1.0, 0.0, 0.0]]
    assert "api.voyageai.com" in fake_api.requests[0]["url"]
    assert llm.embedding_model() == "voyage-3.5-lite"


# ------------------------------------------------------- matching logic (b)
def test_match_picks_cosine_nearest_claim(con):
    claim_vectors = {1: [1.0, 0.0, 0.0], 2: [0.0, 1.0, 0.0]}
    # Post about postal votes, narrative classified as 'none': pure cosine.
    best = pipeline._match_claim("egal", "none", _claims(con),
                                 item_vector=[1.0, 0.0, 0.0], claim_vectors=claim_vectors)
    assert best is not None
    claim, sim = best
    assert claim["id"] == 1
    assert sim == 0.99  # 1.0 cosine, capped like the word-overlap path

    # Below threshold: orthogonal to both claims, no narrative match -> None.
    assert pipeline._match_claim("egal", "none", _claims(con),
                                 item_vector=[0.0, 0.0, 1.0],
                                 claim_vectors=claim_vectors) is None


def test_match_respects_narrative_prior(con):
    claim_vectors = {1: [1.0, 0.0, 0.0], 2: [0.0, 1.0, 0.0]}
    # Cosine alone prefers claim 2 (0.8 vs 0.6), but the +0.4 narrative prior
    # on claim 1 flips it: 0.6 + 0.4 = 1.0 > 0.8.
    best = pipeline._match_claim("egal", "briefwahl_betrug", _claims(con),
                                 item_vector=[0.6, 0.8, 0.0], claim_vectors=claim_vectors)
    claim, sim = best
    assert claim["id"] == 1
    assert sim == 0.99


# ------------------------------------------------------- no-key fallback (c)
def test_no_key_means_no_vectors_and_word_overlap_unchanged(con, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    assert llm.embedding_model() is None
    assert llm.embed_texts(["hallo"]) is None
    assert pipeline.ensure_claim_embeddings(con, _claims(con)) is None
    assert con.execute("SELECT COUNT(*) c FROM claim_embeddings").fetchone()["c"] == 0

    # Word-overlap fallback, byte-identical to the pre-M4.3 behavior:
    # all 5 claim words overlap (1.0) + 0.4 narrative prior -> capped at 0.99.
    best = pipeline._match_claim(
        "die briefwahl wird massenhaft gefälscht sagen alle",
        "briefwahl_betrug", _claims(con))
    claim, sim = best
    assert claim["id"] == 1
    assert sim == 0.99
    # No overlap and no narrative match -> None, exactly as before.
    assert pipeline._match_claim("völlig anderes thema", "none", _claims(con)) is None


def test_mock_pipeline_records_embed_standin(con, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    con.execute(db.sql_insert_ignore(
        "INTO content_items (platform, platform_item_id, author_pseudo, author_followers_band, "
        "text, views, views_prev) "
        "VALUES ('tiktok', 'emb-1', 'pseudo_abc', '10k-100k', "
        "'Die Briefwahl ist Wahlbetrug, Stimmzettel gefälscht!', 60000, 30000)"))
    con.commit()
    pipeline.run_pipeline(con)
    row = con.execute(
        "SELECT e.output_json FROM enrichments e JOIN content_items i ON i.id=e.item_id "
        "WHERE i.platform_item_id='emb-1' AND e.stage='embed'").fetchone()
    assert json.loads(row["output_json"]) == {"model": "mock-standin", "dim": 0}
