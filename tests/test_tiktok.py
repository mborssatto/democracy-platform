"""TikTok Research API client tests — fully offline via httpx.MockTransport.

Proves the request/response mapping of app/tiktok_client.py against a recorded
fixture (mock_data/tiktok_api_response.json) and that the feature flag off
leaves connectors.fetch_tiktok() serving the existing mock data unchanged.
"""
import json
from pathlib import Path

import httpx
import pytest

from app import connectors, tiktok_client

FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "mock_data" / "tiktok_api_response.json")
    .read_text(encoding="utf-8")
)
TOKEN_RESPONSE = {"access_token": "clt.test", "expires_in": 7200, "token_type": "Bearer"}


@pytest.fixture(autouse=True)
def _reset_token_cache():
    """The token cache is module-level state — isolate every test."""
    tiktok_client._token_cache.clear()
    yield
    tiktok_client._token_cache.clear()


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "test-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "test-secret")


@pytest.fixture
def transport():
    """MockTransport serving the oauth token and the recorded query fixture.

    Records every request (and oauth hit count) for assertions.
    """
    state = {"requests": [], "oauth_hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.url.path == "/v2/oauth/token/":
            state["oauth_hits"] += 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        if request.url.path == "/v2/research/video/query/":
            return httpx.Response(200, json=FIXTURE)
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    mock = httpx.MockTransport(handler)
    mock.state = state
    return mock


def _client(transport):
    return httpx.Client(transport=transport)


def test_query_request_shape(creds, transport):
    """(a) Query body has the documented and/IN/EQ structure + Bearer header."""
    with _client(transport) as client:
        tiktok_client.query_videos(client, ["briefwahl", "wahlbetrug"], region="DE")

    query_req = next(
        r for r in transport.state["requests"]
        if r.url.path == "/v2/research/video/query/"
    )
    assert query_req.headers["Authorization"] == "Bearer clt.test"
    assert "id,username,create_time" in query_req.url.params["fields"]

    body = json.loads(query_req.content)
    conditions = body["query"]["and"]
    assert {
        "operation": "IN",
        "field_name": "hashtag_name",
        "field_values": ["briefwahl", "wahlbetrug"],
    } in conditions
    assert {
        "operation": "EQ",
        "field_name": "region_code",
        "field_values": ["DE"],
    } in conditions
    assert body["max_count"] == 100
    assert len(body["start_date"]) == 8 and body["start_date"].isdigit()
    assert len(body["end_date"]) == 8 and body["end_date"].isdigit()

    token_req = next(
        r for r in transport.state["requests"] if r.url.path == "/v2/oauth/token/"
    )
    assert b"grant_type=client_credentials" in token_req.content
    assert b"client_key=test-key" in token_req.content


def test_fetch_real_maps_fields(creds, transport, monkeypatch):
    """(b) fetch_real() returns the exact connector shape, spot-checked."""
    with _client(transport) as client:
        items = tiktok_client.fetch_real(client)

    assert len(items) == 3
    first = items[0]
    assert first == {
        "platform": "tiktok",
        "platform_item_id": "7301112223334445001",
        "real_author_id": "patriot_klartext_88",
        "followers_band": "unknown",
        "text": FIXTURE["data"]["videos"][0]["video_description"],
        "text_en": None,
        "url": "https://www.tiktok.com/@user/video/7301112223334445001",
        "posted_at": "2026-07-13T18:22:00",  # epoch 1783966920 -> ISO (UTC)
        "views": 184000,
        "views_prev": 184000,  # no history in the API: seeded = current
        "likes": 21500,
        "comments": 3400,
        "shares": 8900,
    }
    # Same keys as the mock path produces — pipeline can't tell them apart.
    # (Drop the creds first so fetch_tiktok() takes the offline mock path.)
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
    mock_keys = set(connectors.fetch_tiktok()[0].keys())
    assert set(first.keys()) == mock_keys


def test_flag_off_keeps_mock_behavior(monkeypatch):
    """(c) Without credentials, fetch_tiktok() serves the existing mock fixture."""
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)

    items = connectors.fetch_tiktok()
    mock_fixture = json.loads(
        (Path(__file__).resolve().parent.parent / "mock_data" / "tiktok_videos.json")
        .read_text(encoding="utf-8")
    )
    assert len(items) == len(mock_fixture)
    assert items[0]["platform_item_id"] == str(mock_fixture[0]["id"])
    assert items[0]["views_prev"] == mock_fixture[0]["view_count_24h_ago"]
    assert items[0]["followers_band"] == mock_fixture[0]["followers_band"]


def test_token_cached_across_calls(creds, transport):
    """(d) Two query calls, one oauth round-trip: the token cache works."""
    with _client(transport) as client:
        tiktok_client.query_videos(client, ["briefwahl"])
        tiktok_client.query_videos(client, ["wahlbetrug"])

    assert transport.state["oauth_hits"] == 1
    query_hits = [
        r for r in transport.state["requests"]
        if r.url.path == "/v2/research/video/query/"
    ]
    assert len(query_hits) == 2
