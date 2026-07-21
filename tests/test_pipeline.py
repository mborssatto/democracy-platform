"""End-to-end pipeline test on the mock fixtures, incl. the pseudonymization canary."""
import json

import pytest

from app import connectors, db, llm, pipeline, pseudo


@pytest.fixture()
def con(fresh_db):
    c = db.connect()
    pipeline.seed_reference_data(c)
    yield c
    c.close()


def _ingest_all(con):
    for fetch in (connectors.fetch_tiktok, connectors.fetch_junkipedia, connectors.fetch_tipline):
        for item in fetch():
            p = pseudo.pseudonymize(item["real_author_id"])
            pseudo.store_mapping(con, item["real_author_id"], p)
            con.execute(
                db.sql_insert_ignore(
                    "INTO content_items (platform, platform_item_id, author_pseudo, "
                    "author_followers_band, text, text_en, url, posted_at, ingested_at, views, views_prev, "
                    "likes, comments, shares) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
                (item["platform"], item["platform_item_id"], p, item["followers_band"], item["text"],
                 item["text_en"], item["url"], item["posted_at"], db.now(), item["views"],
                 item["views_prev"], item["likes"], item["comments"], item["shares"]))
    con.commit()


def test_pipeline_end_to_end(con):
    _ingest_all(con)
    log = pipeline.run_pipeline(con)
    assert log["triage_dropped"] >= 1          # the cheesecake decoy is filtered out
    assert log["classified"] >= 8
    assert log["claim_matched"] >= 4           # fact-checks matched
    assert log["opportunities"] >= 4
    # Amplification floor: the <1k-follower 340-view post must NOT become an opportunity
    low = con.execute("SELECT id FROM content_items WHERE views < 5000 AND platform='tiktok'").fetchall()
    for r in low:
        assert con.execute("SELECT 1 FROM opportunities WHERE item_id=?", (r["id"],)).fetchone() is None


def test_pipeline_is_idempotent(con):
    _ingest_all(con)
    pipeline.run_pipeline(con)
    n1 = con.execute("SELECT COUNT(*) c FROM opportunities").fetchone()["c"]
    pipeline.run_pipeline(con)   # second run: no pending items, nothing duplicated
    n2 = con.execute("SELECT COUNT(*) c FROM opportunities").fetchone()["c"]
    assert n1 == n2


def test_canary_no_raw_author_ids_in_main_tables(con):
    """The pseudonymization canary: real handles must never appear outside the vault."""
    _ingest_all(con)
    pipeline.run_pipeline(con)
    canaries = ["patriot_klartext_88", "wahrheit_ungefiltert", "deutschland_erwacht"]
    for table in ("content_items", "enrichments", "opportunities", "audit_log"):
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        blob = json.dumps([dict(r) for r in rows], ensure_ascii=False)
        for canary in canaries:
            assert canary not in blob, f"raw author id leaked into {table}"


# ------------------------------------------------------- summarize stage (M7)
def test_summarize_stage_for_every_processed_item(con):
    """Stage 0 runs BEFORE the triage gate: even triaged-out items carry a
    summary + topic — the ingest cards show every analyzed post."""
    _ingest_all(con)
    log = pipeline.run_pipeline(con)
    assert log["summarized"] > 0
    rows = con.execute(
        "SELECT id, status FROM content_items WHERE status != 'pending'").fetchall()
    assert rows
    for r in rows:
        e = con.execute(
            "SELECT output_json FROM enrichments WHERE item_id=? AND stage='summarize'",
            (r["id"],)).fetchone()
        assert e is not None, f"item {r['id']} ({r['status']}) has no summary"
        out = json.loads(e["output_json"])
        assert out["summary"] and out["topic"]


def test_summarize_mock_topic_maps_narrative():
    out = llm.summarize("Die Briefwahl ist gefälscht! Stimmzettel verschwinden.")
    assert out["topic"] == "elections"
    assert out["title"] and len(out["title"].split()) <= 8  # card headline
    out2 = llm.summarize("[SPEECH 0.0-2.0s] Hallo\n[CAPTION] Rezept für Käsekuchen")
    assert out2["topic"] == "general"
    assert "[SPEECH" not in out2["summary"]  # channel labels stripped


def test_summarize_backfill_for_preexisting_media_items(con):
    """Media items enriched before the stage existed get their summary on the
    next pipeline run (idempotent backfill)."""
    cur = con.execute(
        "INSERT INTO content_items (platform, platform_item_id, author_pseudo, "
        "author_followers_band, text, url, posted_at, ingested_at, views, views_prev, status) "
        "VALUES ('tiktok', 'pre-summarize', 'anon_x', '100k+', "
        "'[SPEECH 0.0-2.0s] Briefwahl Betrug\n[CAPTION] Wahlbetrug!', "
        "'https://tiktok.com/@a/video/9', ?, ?, 90000, 0, 'enriched')",
        (db.now(), db.now()))
    item_id = cur.lastrowid
    con.execute(
        "INSERT INTO media_extractions (item_id, extractor, has_speech, speech_json, "
        "on_screen_json, caption, merged_text, created_at) "
        "VALUES (?, 'mock', 1, '[]', '[]', 'Wahlbetrug!', 'x', ?)",
        (item_id, db.now()))
    con.commit()
    pipeline.run_pipeline(con)
    e = con.execute(
        "SELECT output_json FROM enrichments WHERE item_id=? AND stage='summarize'",
        (item_id,)).fetchone()
    assert e is not None
    assert json.loads(e["output_json"])["topic"] == "elections"
    # idempotent: a second run doesn't duplicate (UNIQUE(item_id, stage))
    pipeline.run_pipeline(con)
    n = con.execute(
        "SELECT COUNT(*) c FROM enrichments WHERE item_id=? AND stage='summarize'",
        (item_id,)).fetchone()["c"]
    assert n == 1
