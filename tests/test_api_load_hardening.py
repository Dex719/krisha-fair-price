"""Поведение API под потоком людей: кэши, single-flight, лимиты.

Здесь проверяется не «быстро ли», а «сколько раз мы полезли в дорогое место»:
скорость на CI не воспроизводится, а количество вызовов — воспроизводится
точно и ловит регрессию (кто-то убрал кэш) так же надёжно.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from krisha import db, predict_gate
from krisha.api import app as app_module
from krisha.api.app import app
from krisha.api.cache import TTLCache
from krisha.db import get_conn

NOW = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)


def _seed(db_path, listing_id: int = 9001, last_seen: datetime | None = None) -> None:
    db.init_db(db_path)
    db.upsert_listing(
        {
            "id": listing_id,
            "url": f"https://krisha.kz/a/show/{listing_id}",
            "title": "Лот",
            "price": 42_000_000,
            "area": 55.0,
            "rooms": 2,
            "district": "Auezovskiy_r-n",
            "source": "test",
        },
        db_path=db_path,
    )
    observed = (last_seen or NOW - timedelta(hours=2)).astimezone(timezone.utc)
    stamp = observed.replace(tzinfo=None).isoformat(sep=" ")
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE listings SET last_seen = ?, scraped_at = ? WHERE id = ?",
            (stamp, stamp, listing_id),
        )


# --------------------------------------------------------------------------
# TTLCache
# --------------------------------------------------------------------------
def test_cache_calls_producer_once_while_fresh():
    calls = []
    cache = TTLCache(ttl=60)
    for _ in range(5):
        cache.get_or_call("k", lambda: calls.append(1) or "value")
    assert len(calls) == 1


def test_cache_recomputes_after_ttl():
    cache = TTLCache(ttl=0.05)
    assert cache.get_or_call("k", lambda: "первый") == "первый"
    time.sleep(0.08)
    assert cache.get_or_call("k", lambda: "второй") == "второй"


def test_cache_single_flight_under_parallel_load():
    """Сто параллельных читателей — один пересчёт, а не сто."""
    calls: list[int] = []
    cache = TTLCache(ttl=60)
    start = threading.Barrier(20)

    def producer():
        calls.append(1)
        time.sleep(0.05)  # имитируем тяжёлый запрос к базе
        return "готово"

    results: list[str] = []

    def worker():
        start.wait()
        results.append(cache.get_or_call("stats", producer))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == ["готово"] * 20
    assert len(calls) == 1


def test_cache_serves_stale_value_when_recompute_fails():
    cache = TTLCache(ttl=0.05, stale_ttl=60)
    assert cache.get_or_call("k", lambda: "старое") == "старое"
    time.sleep(0.08)

    def broken():
        raise RuntimeError("база занята")

    assert cache.get_or_call("k", broken) == "старое"


def test_cache_without_stale_ttl_reraises():
    cache = TTLCache(ttl=0.01)
    cache.get_or_call("k", lambda: "старое")
    time.sleep(0.02)
    with pytest.raises(RuntimeError):
        cache.get_or_call("k", lambda: (_ for _ in ()).throw(RuntimeError("нет базы")))


def test_cache_evicts_beyond_maxsize():
    cache = TTLCache(ttl=60, maxsize=3)
    for i in range(10):
        cache.get_or_call(i, lambda i=i: i)
    assert len(cache) <= 3


# --------------------------------------------------------------------------
# /api/health
# --------------------------------------------------------------------------
def test_health_reads_database_once_per_ttl(tmp_path, monkeypatch):
    db_path = tmp_path / "health.db"
    _seed(db_path)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    monkeypatch.setattr(app_module, "_utcnow", lambda: NOW)
    reads = []
    real = app_module._data_freshness
    monkeypatch.setattr(
        app_module, "_data_freshness", lambda: (reads.append(1), real())[1]
    )

    client = TestClient(app)
    first = client.get("/api/health").json()
    for _ in range(9):
        assert client.get("/api/health").json() == first

    assert len(reads) == 1, "MAX(last_seen) по всей базе на каждый запрос — это и был потолок"
    assert first["data_age_hours"] == pytest.approx(2.0, abs=0.02)


def test_health_answers_are_cacheable_by_browser(tmp_path, monkeypatch):
    db_path = tmp_path / "health.db"
    _seed(db_path)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)

    resp = TestClient(app).get("/api/health")

    assert resp.headers["cache-control"] == "public, max-age=60"


def test_health_survives_broken_database_with_last_known_answer(tmp_path, monkeypatch):
    db_path = tmp_path / "health.db"
    _seed(db_path)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    monkeypatch.setattr(app_module, "_utcnow", lambda: NOW)
    client = TestClient(app)
    good = client.get("/api/health").json()

    # TTL истёк, а база в этот момент недоступна
    app_module._freshness_cache.ttl = 0.0
    monkeypatch.setattr(
        app_module, "_data_freshness", lambda: (_ for _ in ()).throw(RuntimeError("занята"))
    )
    try:
        stale = client.get("/api/health").json()
    finally:
        app_module._freshness_cache.ttl = app_module.HEALTH_CACHE_TTL_S

    assert stale["data_age_hours"] == good["data_age_hours"]
    assert stale["freshness"] == good["freshness"]


def test_listings_have_index_on_last_seen(tmp_path):
    """Без индекса MAX(last_seen) читает всю таблицу — 80 мс на проде."""
    db_path = tmp_path / "idx.db"
    _seed(db_path)
    with get_conn(db_path) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT MAX(last_seen) FROM listings WHERE last_seen IS NOT NULL"
        ).fetchall()
    assert any("idx_listings_last_seen" in str(tuple(row)) for row in plan), plan


# --------------------------------------------------------------------------
# /api/stats
# --------------------------------------------------------------------------
def test_stats_computed_once_for_parallel_requests(monkeypatch):
    calls: list[int] = []

    def slow_stats():
        calls.append(1)
        time.sleep(0.05)
        return {"total": 1}

    monkeypatch.setattr(app_module, "get_stats", slow_stats)
    client = TestClient(app)
    results: list[dict] = []
    threads = [
        threading.Thread(target=lambda: results.append(client.get("/api/stats").json()))
        for _ in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [{"total": 1}] * 10
    assert len(calls) == 1


def test_stats_serves_last_good_answer_when_database_disappears(monkeypatch):
    monkeypatch.setattr(app_module, "get_stats", lambda: {"total": 7})
    client = TestClient(app)
    assert client.get("/api/stats").json() == {"total": 7}

    app_module._stats_cache.ttl = 0.0
    monkeypatch.setattr(
        app_module, "get_stats", lambda: (_ for _ in ()).throw(FileNotFoundError("нет базы"))
    )
    try:
        resp = client.get("/api/stats")
    finally:
        app_module._stats_cache.ttl = app_module.STATS_CACHE_TTL

    assert resp.status_code == 200
    assert resp.json() == {"total": 7}


# --------------------------------------------------------------------------
# /api/demo
# --------------------------------------------------------------------------
def test_demo_does_not_scan_whole_table_per_request(tmp_path, monkeypatch):
    db_path = tmp_path / "demo.db"
    for i in range(5):
        _seed(db_path, listing_id=9100 + i)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    queries: list[int] = []
    real_pool = app_module._demo_pool
    monkeypatch.setattr(app_module, "_demo_pool", lambda: (queries.append(1), real_pool())[1])

    client = TestClient(app)
    seen = set()
    for _ in range(10):
        payload = client.get("/api/demo").json()
        seen.add(payload["listing_id"])

    assert len(queries) == 1, "пул кандидатов должен браться из базы один раз на TTL"
    assert seen, "демо-лот должен возвращаться"
    assert all(str(i).isdigit() for i in seen)


def test_demo_pool_query_uses_index(tmp_path):
    db_path = tmp_path / "demo_plan.db"
    _seed(db_path)
    with get_conn(db_path) as conn:
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT id, url FROM listings
            WHERE is_active = 1 AND url IS NOT NULL AND url LIKE '%krisha.kz/a/show/%'
            ORDER BY last_seen DESC LIMIT 100
            """
        ).fetchall()
    text = " ".join(str(tuple(row)) for row in plan)
    assert "idx_listings_last_seen" in text, text
    assert "TEMP B-TREE" not in text, text


# --------------------------------------------------------------------------
# /api/predict
# --------------------------------------------------------------------------
def _payload(listing_id: int = 91) -> dict:
    return {
        "listing_id": listing_id,
        "url": f"https://krisha.kz/a/show/{listing_id}",
        "title": "Квартира",
        "address": "Алматы",
        "actual_price": 48_000_000,
        "fair_price": 47_000_000.0,
        "fair_price_low": 44_000_000.0,
        "fair_price_high": 50_000_000.0,
        "verdict": "FAIR",
        "diff_pct": 2.1,
        "top_factors": [],
        "details": [],
        "complex_details": [],
        "location_details": [],
        "price_history": [],
        "days_on_market": None,
        "liquidity": None,
        "text_flags": [],
        "flags_pending": False,
        "duplicate_of": None,
        "photos": [],
        "description": None,
        "analogs": [],
        "scam_risk": None,
        "renovation": None,
    }


def test_same_listing_is_fetched_once_for_the_crowd(monkeypatch):
    """Пост в канале: тысяча человек вставляет одну ссылку — один разбор."""
    calls: list[str] = []

    def slow_predict(url, live_vision=False, timeout=None):
        calls.append(url)
        time.sleep(0.05)
        return _payload()

    monkeypatch.setattr(predict_gate, "predict_from_url", slow_predict)
    monkeypatch.setattr(app_module, "RATE_LIMIT", 10_000)
    client = TestClient(app)

    def hit(url):
        assert client.post("/api/predict", json={"url": url}).status_code == 200

    threads = [
        threading.Thread(target=hit, args=("https://krisha.kz/a/show/91",)) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # и ещё десять «опоздавших» уже после того, как ответ посчитан
    for _ in range(10):
        hit("https://krisha.kz/a/show/91?utm_source=telegram")

    assert len(calls) == 1, f"на krisha.kz ушло {len(calls)} запросов вместо одного"


def test_different_listings_are_not_confused(monkeypatch):
    seen: list[str] = []

    def fake_predict(url, live_vision=False, timeout=None):
        seen.append(url)
        return _payload(int(url.rsplit("/", 1)[-1]))

    monkeypatch.setattr(predict_gate, "predict_from_url", fake_predict)
    client = TestClient(app)

    first = client.post("/api/predict", json={"url": "https://krisha.kz/a/show/91"}).json()
    second = client.post("/api/predict", json={"url": "https://krisha.kz/a/show/92"}).json()

    assert first["listing_id"] == 91
    assert second["listing_id"] == 92
    assert len(seen) == 2


def test_failed_predict_is_retried_after_the_negative_cache_expires(monkeypatch):
    """Ошибка запоминается на минуту (иначе битая ссылка из поста гонит
    каждого посетителя в полный скрейп-цикл), но не навсегда."""
    attempts: list[int] = []

    def flaky(url, live_vision=False, timeout=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("krisha не ответила")
        return _payload()

    monkeypatch.setattr(predict_gate, "predict_from_url", flaky)
    client = TestClient(app)

    assert client.post("/api/predict", json={"url": "https://krisha.kz/a/show/91"}).status_code == 502
    # пока негативный кэш свеж — тот же 502 без похода наружу
    assert client.post("/api/predict", json={"url": "https://krisha.kz/a/show/91"}).status_code == 502
    assert len(attempts) == 1

    predict_gate._negative.clear()
    assert client.post("/api/predict", json={"url": "https://krisha.kz/a/show/91"}).status_code == 200
    assert len(attempts) == 2


def test_cached_predict_still_counts_in_usage_stats(monkeypatch):
    monkeypatch.setattr(predict_gate, "predict_from_url", lambda url, live_vision=False, timeout=None: _payload())
    events: list[str] = []
    monkeypatch.setattr(app_module.usage, "record_event", lambda kind, *a, **k: events.append(kind))
    client = TestClient(app)

    for _ in range(3):
        client.post("/api/predict", json={"url": "https://krisha.kz/a/show/91"})

    assert events.count("predict") == 3, "кэш не должен обнулять статистику визитов"


# --------------------------------------------------------------------------
# Лимиты
# --------------------------------------------------------------------------
def test_rate_limit_answers_with_retry_after(monkeypatch):
    monkeypatch.setattr(predict_gate, "predict_from_url", lambda url, live_vision=False, timeout=None: _payload())
    app_module._rate.clear()
    client = TestClient(app)
    for i in range(app_module.RATE_LIMIT):
        client.post("/api/predict", json={"url": f"https://krisha.kz/a/show/{i}"})

    resp = client.post("/api/predict", json={"url": "https://krisha.kz/a/show/999"})

    assert resp.status_code == 429
    assert 1 <= int(resp.headers["retry-after"]) <= 61
    app_module._rate.clear()


def test_rate_limit_is_configurable_by_environment(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_WINDOW", "300")
    assert app_module._env_int("RATE_LIMIT_PER_WINDOW", 15) == 300
    monkeypatch.setenv("RATE_LIMIT_PER_WINDOW", "мусор")
    assert app_module._env_int("RATE_LIMIT_PER_WINDOW", 15) == 15
