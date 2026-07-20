"""Zeeschuimer NDJSON import (TikTok/Instagram without Research-API access)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import connectors, db, main, pipeline
from conftest import login_headers

FIXTURE = (Path(__file__).resolve().parent.parent / "mock_data" / "zeeschuimer_export.ndjson").read_text()


@pytest.fixture()
def client(fresh_db):
    con = db.connect()
    pipeline.seed_reference_data(con)
    con.close()
    with TestClient(main.app, raise_server_exceptions=False) as c:
        c.headers.update(login_headers(c, "staff"))
        yield c


def test_parse_maps_both_platforms():
    items, skipped = connectors.parse_zeeschuimer(FIXTURE)
    assert len(items) == 4 and skipped == 0
    tiktok = items[0]
    assert tiktok["platform"] == "tiktok"
    assert tiktok["views"] == 128000 and tiktok["views_prev"] == 128000
    assert tiktok["followers_band"] == "10k-100k"          # banded, never exact
    assert "wahrheits_kanal_de" not in tiktok["url"]       # no handle in stored URL
    insta = items[2]
    assert insta["platform"] == "instagram"
    assert insta["text"].startswith("Die EU führt")
    assert insta["followers_band"] == "100k+"
    assert insta["url"] == "https://www.instagram.com/p/DEmoCr4cyXx/"
    photo = items[3]
    assert photo["views"] == 0                              # no view count → never invented


def test_parse_skips_garbage_lines():
    items, skipped = connectors.parse_zeeschuimer(
        'not json at all\n{"source_platform": "youtube.com", "data": {}}\n')
    assert items == [] and skipped == 2


def test_import_endpoint_inserts_pseudonymized_and_enqueues(client):
    r = client.post("/api/import/zeeschuimer", json={"ndjson": FIXTURE})
    assert r.status_code == 200
    body = r.json()
    assert body["new_items"] == 4 and body["enqueued"] == 4 and body["skipped_lines"] == 0

    con = db.connect()
    rows = con.execute("SELECT * FROM content_items WHERE platform IN ('tiktok','instagram')").fetchall()
    assert len(rows) == 4
    blob = str([dict(r) for r in rows])
    for handle in ("wahrheits_kanal_de", "freiheit_zuerst_ig", "klima_skeptiker_88", "backfee_lisa"):
        assert handle not in blob, "raw handle leaked into content_items"
    jobs = con.execute("SELECT COUNT(*) c FROM jobs WHERE kind='enrich_item'").fetchone()["c"]
    con.close()
    assert jobs == 4


def test_import_is_idempotent(client):
    client.post("/api/import/zeeschuimer", json={"ndjson": FIXTURE})
    r = client.post("/api/import/zeeschuimer", json={"ndjson": FIXTURE})
    assert r.json()["new_items"] == 0                       # duplicates skipped


def test_imported_items_flow_through_pipeline(client):
    client.post("/api/import/zeeschuimer", json={"ndjson": FIXTURE})
    client.post("/api/pipeline/run")
    radar = client.get("/api/radar").json()
    codes = {n["code"] for n in radar}
    assert "eu_bargeldverbot" in codes                      # the Instagram reel classified in
    con = db.connect()
    dropped = con.execute("SELECT COUNT(*) c FROM content_items WHERE status='triaged_out'").fetchone()["c"]
    con.close()
    assert dropped >= 1                                     # the cheesecake video


def test_import_requires_staff(client):
    responder = login_headers(client, "responder")
    r = client.post("/api/import/zeeschuimer", json={"ndjson": FIXTURE}, headers=responder)
    assert r.status_code == 403
