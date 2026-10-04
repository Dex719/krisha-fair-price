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
    from krisha.db import init_db

    (tmp_path / "m.cbm").write_bytes(b"x")
    # Настоящая база, а не байт-заглушка: старт гонит по ней миграции
    # (init_db(DB_PATH)), и раньше они молча шли в data/krisha.db рабочей копии.
    init_db(tmp_path / "k.db")
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


# --- sitemap: настоящий lastmod, один домен со страницами ------------------------

def _lastmods(xml: str) -> dict[str, str | None]:
    import re

    out = {}
    for url in re.findall(r"<url>(.*?)</url>", xml):
        loc = re.search(r"<loc>https?://[^/]+(/[^<]*)</loc>", url).group(1)
        mod = re.search(r"<lastmod>([^<]+)</lastmod>", url)
        out[loc] = mod.group(1) if mod else None
    return out


def test_sitemap_lastmod_is_file_date_or_data_date(tmp_path, monkeypatch):
    """Раньше lastmod всегда был «сегодня». Теперь у статичных страниц — дата
    файла, у страниц с данными рынка — не раньше последнего прохода сборщика."""
    import os
    from datetime import datetime, timezone

    from krisha.db import get_conn, init_db

    db_path = tmp_path / "k.db"
    init_db(db_path)
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO sweep_runs (started_at, deal, search_seconds, detail_seconds) "
            "VALUES ('2099-01-02 03:00:00', 'prodazha', 60, 60)"
        )
    monkeypatch.setattr(app_module, "DB_PATH", db_path)

    mods = _lastmods(TestClient(app).get("/sitemap.xml").text)

    def file_date(name):
        ts = os.stat(app_module.STATIC_DIR / name).st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()

    assert mods["/about"] == file_date("about.html")
    assert mods["/terms"] == file_date("terms.html")
    assert mods["/stats"] == "2099-01-02" and mods["/"] == "2099-01-02"
    assert "/rent" not in mods


def test_sitemap_without_public_base_url_uses_canonical_domain(monkeypatch):
    """Без PUBLIC_BASE_URL sitemap брал домен Space (SPACE_HOST), а canonical
    страниц — bagam.info: поисковик видел два адреса одного сайта."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("SPACE_HOST", "dex719-krisha-fair-price.hf.space")
    with TestClient(app) as client:
        sitemap = client.get("/sitemap.xml").text
        robots = client.get("/robots.txt").text

    assert "<loc>https://bagam.info/stats</loc>" in sitemap
    assert "hf.space" not in sitemap
    assert "Sitemap: https://bagam.info/sitemap.xml" in robots


# --- кэш JSON у браузера: max-age + ETag + Vary -----------------------------------

def test_stats_json_is_revalidated_by_etag_with_vary(monkeypatch):
    payload = {"total_listings": 7, "by_district": [{"district": "Бостандыкский", "n": i} for i in range(80)]}
    monkeypatch.setattr(app_module, "get_stats", lambda: payload)
    client = TestClient(app)

    gz = client.get("/api/stats", headers={"accept-encoding": "gzip"})
    raw = client.get("/api/stats", headers={"accept-encoding": "identity"})

    assert gz.json() == raw.json() == payload
    assert gz.headers["cache-control"] == "public, max-age=300"
    assert gz.headers["vary"] == "Accept-Encoding"
    assert gz.headers["content-encoding"] == "gzip"
    # у сжатого и несжатого представлений — разные ETag (как у статики)
    assert gz.headers["etag"] != raw.headers["etag"] and gz.headers["etag"].endswith('-gz"')

    again = client.get("/api/stats", headers={"accept-encoding": "gzip", "if-none-match": gz.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    assert again.headers["cache-control"] == "public, max-age=300"
    assert "Accept-Encoding" in again.headers["vary"]


def test_health_and_rent_stats_answer_304_on_matching_etag(monkeypatch):
    monkeypatch.setattr(app_module, "compute_rent_stats", lambda: {"total_listings": 3})
    client = TestClient(app)
    for path, max_age in (("/api/health", 60), ("/api/stats/rent", 300)):
        first = client.get(path)
        assert first.status_code == 200, path
        assert first.headers["cache-control"] == f"public, max-age={max_age}", path
        assert "Accept-Encoding" in first.headers["vary"], path
        second = client.get(path, headers={"if-none-match": first.headers["etag"]})
        assert second.status_code == 304, path
