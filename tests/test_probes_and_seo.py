"""issue #190 §2.6–2.7: пробники без внешних зависимостей, request id,
robots.txt и sitemap.xml (оба раньше отдавали 404)."""

import json

from fastapi.testclient import TestClient

from krisha.api import app as app_module
from krisha.api.app import app


def test_livez_is_trivially_ok():
    with TestClient(app) as client:
        r = client.get("/livez")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["Cache-Control"] == "no-store"


def test_readyz_reports_missing_artifacts_as_503(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MODEL_PATH", tmp_path / "nope.cbm")
    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "nope.db")
    with TestClient(app) as client:
        r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["model"] is False and body["checks"]["db"] is False


def test_readyz_ok_when_model_and_db_exist(tmp_path, monkeypatch):
    (tmp_path / "m.cbm").write_bytes(b"x")
    (tmp_path / "k.db").write_bytes(b"x")
    monkeypatch.setattr(app_module, "MODEL_PATH", tmp_path / "m.cbm")
    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "k.db")
    with TestClient(app) as client:
        r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_request_id_is_echoed_or_generated():
    with TestClient(app) as client:
        own = client.get("/livez", headers={"X-Request-ID": "req-abc_1.2"})
        generated = client.get("/livez")
        junk = client.get("/livez", headers={"X-Request-ID": "bad id <script>"})
    assert own.headers["X-Request-ID"] == "req-abc_1.2"
    assert len(generated.headers["X-Request-ID"]) == 32
    assert junk.headers["X-Request-ID"] != "bad id <script>"


def test_robots_and_sitemap_exist(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bagam.example")
    with TestClient(app) as client:
        robots = client.get("/robots.txt")
        sitemap = client.get("/sitemap.xml")
    assert robots.status_code == 200
    assert robots.headers["content-type"].startswith("text/plain")
    assert "Disallow: /api/" in robots.text
    assert "Sitemap: https://bagam.example/sitemap.xml" in robots.text
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    for path in ("/", "/stats", "/about"):
        assert f"<loc>https://bagam.example{path}</loc>" in sitemap.text


def test_health_exposes_mape_ci(tmp_path, monkeypatch):
    meta = {"metrics": {"model": {"mape": 0.0749}, "model_mape_ci": {"lo": 0.0728, "hi": 0.0772}}}
    path = tmp_path / "model_meta.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(app_module, "MODEL_META_PATH", path)
    app_module._model_meta_cache.clear()
    with TestClient(app) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["model_error_ci_pct"] == [7.3, 7.7]
