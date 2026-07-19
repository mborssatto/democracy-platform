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
