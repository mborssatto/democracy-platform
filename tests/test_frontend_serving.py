"""M4.1 frontend serving: / prefers the Nuxt static build when it exists and
falls back to the zero-build page otherwise; /demo ALWAYS serves the old
zero-build page (works with no node toolchain)."""
import pytest
from fastapi.testclient import TestClient

from app import db, main, pipeline


@pytest.fixture()
def client(fresh_db):
    con = db.connect()
    pipeline.seed_reference_data(con)
    con.close()
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


def test_demo_always_serves_zero_build_page(client):
    r = client.get("/demo")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "vue.global.prod.js" in r.text  # the vendored-Vue page, not the SPA


def test_root_without_nuxt_build_falls_back_to_old_page(client, monkeypatch):
    monkeypatch.setattr(main, "_nuxt_built", False)
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "vue.global.prod.js" in r.text


def test_root_with_nuxt_build_serves_it(client, monkeypatch, tmp_path):
    # Simulate a completed `npm run generate` without requiring node in CI.
    fake = tmp_path / "index.html"
    fake.write_text("<!DOCTYPE html><html><head><title>spa</title></head>"
                    "<body>nuxt-spa-build</body></html>")
    monkeypatch.setattr(main, "_nuxt_built", True)
    monkeypatch.setattr(main, "NUXT_INDEX", fake)
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "nuxt-spa-build" in r.text
