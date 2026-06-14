"""Тесты аналитики событий и admin-эндпоинтов dev-панели."""

import importlib

import pytest
from fastapi.testclient import TestClient

from krisha import analytics


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "events.db"
    analytics.init_events(p)
    return p


def _sample(**over):
    base = {
        "listing_id": 1,
        "url": "https://krisha.kz/a/show/1",
        "rooms": 2,
        "area": 60.0,
        "actual_price": 50_000_000,
        "fair_price": 45_000_000.0,
        "verdict": "OVERPRICED",
        "diff_pct": 11.0,
        "details": [{"label": "Район", "value": "Бостандыкский"}],
    }
    base.update(over)
    return base


def test_log_and_aggregate(db):
    analytics.log_event(source="web", visitor_raw="1.1.1.1", result=_sample(), response_ms=300, db_path=db)
    analytics.log_event(
        source="bot", visitor_raw=42,
        result=_sample(verdict="GOOD_DEAL"), response_ms=200, db_path=db,
    )
    analytics.log_event(source="web", visitor_raw="1.1.1.1", status="error", url="x", db_path=db)

    s = analytics.get_admin_stats(days=7, db_path=db)
    assert s["total_requests"] == 3
    assert s["unique_visitors"] == 2          # 1.1.1.1 и 42
    assert s["errors"] == 1
    assert s["by_source"] == {"web": 2, "bot": 1}
    assert s["by_verdict"] == {"GOOD_DEAL": 1, "OVERPRICED": 1}  # ошибка не считается
    assert s["by_district"][0] == {"district": "Бостандыкский", "n": 2}
    assert len(s["by_day"]) == 7
    assert sum(s["by_hour"]) == 3


def test_visitor_hash_is_stable_and_anonymous(db):
    h = analytics.visitor_hash("8.8.8.8")
    assert h == analytics.visitor_hash("8.8.8.8")
    assert "8.8.8.8" not in h
    assert len(h) == 12


def test_log_event_never_raises_on_bad_db():
    # несуществующая директория не должна ронять вызывающий код
    analytics.log_event(source="web", visitor_raw="x", result=_sample(),
                        db_path="/nonexistent_dir_xyz/q/events.db")


def test_admin_endpoints_token_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "k.db"))
    import krisha.api.app as appmod
    importlib.reload(appmod)
    client = TestClient(appmod.app)

    assert client.get("/api/admin/stats").status_code == 403
    assert client.get("/api/admin/stats?token=nope").status_code == 403
    r = client.get("/api/admin/stats?token=secret")
    assert r.status_code == 200
    assert "total_requests" in r.json()
    assert client.get("/api/admin/events?token=secret").status_code == 200

    # M2: токен можно (и нужно) передавать в заголовке Authorization: Bearer
    auth = {"Authorization": "Bearer secret"}
    assert client.get("/api/admin/stats", headers=auth).status_code == 200
    assert client.get("/api/admin/events", headers=auth).status_code == 200
    assert client.get("/api/admin/stats",
                      headers={"Authorization": "Bearer nope"}).status_code == 403


def test_security_headers_present(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "k.db"))
    import krisha.api.app as appmod
    importlib.reload(appmod)
    client = TestClient(appmod.app)
    r = client.get("/api/health")
    assert "Content-Security-Policy" in r.headers
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_admin_disabled_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    import krisha.api.app as appmod
    importlib.reload(appmod)
    client = TestClient(appmod.app)
    assert client.get("/api/admin/stats?token=whatever").status_code == 503
