"""/api/stats/rent: рынок аренды для страницы /rent."""

from fastapi.testclient import TestClient

from krisha.api import app as app_module
from krisha.api.app import app


def test_rent_stats_endpoint_serves_cached_numbers(monkeypatch):
    calls: list[int] = []

    def fake():
        calls.append(1)
        return {"total_listings": 3, "median_rent": 300_000, "by_district": []}

    monkeypatch.setattr(app_module, "compute_rent_stats", fake)
    client = TestClient(app)

    first = client.get("/api/stats/rent")
    second = client.get("/api/stats/rent")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["median_rent"] == 300_000
    assert "max-age=300" in first.headers["cache-control"]
    assert len(calls) == 1, "агрегаты по базе считаются раз в TTL, а не на каждый запрос"


def test_rent_stats_endpoint_is_503_without_rent_database(monkeypatch):
    def missing():
        raise FileNotFoundError("нет базы аренды")

    monkeypatch.setattr(app_module, "compute_rent_stats", missing)
    resp = TestClient(app).get("/api/stats/rent")

    assert resp.status_code == 503
    assert "аренды" in resp.json()["detail"]


def test_new_pages_are_routed_and_in_sitemap():
    client = TestClient(app)
    for path in ("/bot", "/privacy", "/terms"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        # ссылка на стили — с версией содержимого (static_cache.build_site)
        assert 'href="/static/design.css?v=' in resp.text, path
    sitemap = client.get("/sitemap.xml").text
    for path in ("/bot", "/privacy", "/terms"):
        assert f"{path}</loc>" in sitemap, path
    # «Аренда» — режим «Рынка»: /rent ведёт туда и в sitemap не числится
    assert client.get("/rent", follow_redirects=False).status_code == 301
    assert "/rent</loc>" not in sitemap
