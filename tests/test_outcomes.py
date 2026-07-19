"""Outcome snapshot tests (M4.2).

Acceptance: marking a draft posted schedules the +1h/+24h/+72h snapshot jobs;
"running" them (fast-forward endpoint) stores 3 intervened + 3 baseline
snapshots with DETERMINISTIC values; climate_delta reports per-offset and
overall deltas; GET /api/outcomes exposes it all; responders get 403.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import db, main, outcomes, pipeline
from conftest import login_headers

# A genuine human rework (passes the edit gate: high word-level distance and
# plenty of new words; party-neutral so the neutrality gate stays green).
REWORKED = (
    "Hier lohnt ein genauer Blick: der verlinkte Faktencheck erklärt Schritt für "
    "Schritt, warum diese Behauptung so nicht haltbar ist. Lies ihn gern selbst "
    "nach und vergleiche die Quellen in Ruhe."
)


@pytest.fixture()
def client(fresh_db):
    con = db.connect()
    pipeline.seed_reference_data(con)
    con.close()
    with TestClient(main.app, raise_server_exceptions=False) as c:
        c.headers.update(login_headers(c, "staff"))  # staff by default
        yield c


def _post_reply(client) -> int:
    """The full Respond flow end-to-end (as staff): ingest → pipeline → claim →
    draft → genuine rework → mark posted. Returns the posted_action id."""
    assert client.post("/api/ingest/tiktok").status_code == 200
    assert client.post("/api/pipeline/run").status_code == 200
    opp_id = client.get("/api/opportunities").json()[0]["id"]
    assert client.post(f"/api/opportunities/{opp_id}/claim").status_code == 200
    draft_id = client.post(f"/api/opportunities/{opp_id}/draft").json()["draft_id"]
    # Backdate the draft so the 60s dwell gate passes without sleeping.
    con = db.connect()
    con.execute("UPDATE drafts SET created_at=? WHERE id=?", ("2020-01-01T00:00:00", draft_id))
    con.commit(); con.close()
    r = client.put(f"/api/drafts/{draft_id}", json={"text": REWORKED}).json()
    assert r["post_ready"], r["checks"]
    assert client.post(f"/api/drafts/{draft_id}/mark-posted",
                       json={"posted_url": "https://tiktok.com/@me/reply/1"}).status_code == 200
    con = db.connect()
    pa = con.execute("SELECT id FROM posted_actions WHERE draft_id=?", (draft_id,)).fetchone()
    con.close()
    assert pa is not None
    return pa["id"]


def test_mark_posted_schedules_three_future_snapshot_jobs(client):
    pa_id = _post_reply(client)
    con = db.connect()
    jobs = con.execute("SELECT * FROM jobs WHERE kind='outcome_snapshot'").fetchall()
    con.close()
    assert len(jobs) == 3
    payloads = [json.loads(j["payload"]) for j in jobs]
    assert {p["offset"] for p in payloads} == {"+1h", "+24h", "+72h"}
    assert all(p["posted_action_id"] == pa_id for p in payloads)
    # All queued with a run_after in the future (ISO strings compare correctly)
    assert all(j["status"] == "queued" for j in jobs)
    assert all(j["run_after"] > db.now() for j in jobs)


def test_duplicate_mark_posted_does_not_reschedule(client):
    pa_id = _post_reply(client)
    con = db.connect()
    draft_id = con.execute("SELECT draft_id FROM posted_actions WHERE id=?", (pa_id,)).fetchone()["draft_id"]
    # Re-arm the draft and mark posted again: posted_actions is insert-ignore,
    # so no new row — and no second batch of snapshot jobs either.
    con.execute("UPDATE drafts SET status='post_ready' WHERE id=?", (draft_id,))
    con.commit(); con.close()
    assert client.post(f"/api/drafts/{draft_id}/mark-posted",
                       json={"posted_url": "https://tiktok.com/@me/reply/dup"}).status_code == 200
    con = db.connect()
    n = con.execute("SELECT COUNT(*) c FROM jobs WHERE kind='outcome_snapshot'").fetchone()["c"]
    con.close()
    assert n == 3


def test_fast_forward_captures_deterministic_snapshots(client):
    pa_id = _post_reply(client)
    r = client.post("/api/outcomes/fast-forward")
    assert r.status_code == 200
    body = r.json()
    assert body["advanced"] == 3 and body["processed"] >= 3

    con = db.connect()
    rows = con.execute(
        "SELECT * FROM outcome_snapshots WHERE posted_action_id=? ORDER BY id",
        (pa_id,)).fetchall()
    assert len(rows) == 6  # 3 intervened + 3 baseline
    assert sum(1 for r_ in rows if r_["is_baseline"] == 0) == 3
    assert sum(1 for r_ in rows if r_["is_baseline"] == 1) == 3
    # No author fields on snapshot rows (pseudonymization canary territory).
    assert not any("author" in k for k in dict(rows[0]))

    stored = {(r_["offset_label"], r_["is_baseline"]):
              (r_["views"], r_["likes"], r_["comments"], r_["sentiment_score"], r_["reply_count"])
              for r_ in rows}
    # Determinism: a second capture run computes the exact same values (the
    # insert is idempotent, so nothing is duplicated either).
    for offset in ("+1h", "+24h", "+72h"):
        again = outcomes.capture_snapshot(con, pa_id, offset)
        for is_baseline, key in ((0, "intervened"), (1, "baseline")):
            v = again[key]
            assert stored[(offset, is_baseline)] == (
                v["views"], v["likes"], v["comments"], v["sentiment_score"], v["reply_count"])
        # Intervened thread climate beats the projected baseline (mock model).
        assert again["intervened"]["sentiment_score"] > again["baseline"]["sentiment_score"]
    con.commit()
    n = con.execute("SELECT COUNT(*) c FROM outcome_snapshots WHERE posted_action_id=?",
                    (pa_id,)).fetchone()["c"]
    assert n == 6

    # The fast-forward and each capture batch are audited (append-only log).
    actions = [a["action"] for a in con.execute("SELECT action FROM audit_log").fetchall()]
    con.close()
    assert "outcomes_fast_forward" in actions
    assert actions.count("outcome_snapshot_captured") >= 3


def test_climate_delta_reports_three_offsets_and_overall(client):
    pa_id = _post_reply(client)
    client.post("/api/outcomes/fast-forward")
    con = db.connect()
    delta = outcomes.climate_delta(con, pa_id)
    con.close()
    assert [o["offset"] for o in delta["offsets"]] == ["+1h", "+24h", "+72h"]
    for o in delta["offsets"]:
        assert o["sentiment_delta"] > 0
        assert o["views"] > 0 and o["reply_count"] >= 0
    assert delta["overall_sentiment_delta"] == pytest.approx(
        sum(o["sentiment_delta"] for o in delta["offsets"]) / 3, abs=0.001)


def test_outcomes_endpoint_shows_snapshots_and_delta(client):
    pa_id = _post_reply(client)
    client.post("/api/outcomes/fast-forward")
    body = client.get("/api/outcomes").json()
    assert "note" in body
    posted = body["posted"]
    assert len(posted) == 1 and posted[0]["id"] == pa_id
    assert len(posted[0]["snapshots"]) == 6
    assert len(posted[0]["delta"]["offsets"]) == 3
    assert posted[0]["delta"]["overall_sentiment_delta"] > 0


def test_outcomes_endpoints_are_staff_only(client):
    responder = login_headers(client, "responder")
    assert client.get("/api/outcomes", headers=responder).status_code == 403
    assert client.post("/api/outcomes/fast-forward", headers=responder).status_code == 403
