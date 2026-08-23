"""Калитка предикта под наплывом: кэш до лимита, негативный кэш, слоты, бот.

Сценарий один и тот же: ссылку постят в большой канал, за две минуты приходит
тысяча человек, и почти все проверяют ОДНО объявление, сидя за десятком
CGNAT-адресов мобильных операторов.
"""

import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from krisha import bot, predict_gate
from krisha.api import app as app_module
from krisha.api.app import app


def _payload(listing_id: int = 91) -> dict:
    return {
        "listing_id": listing_id,
        "url": f"https://krisha.kz/a/show/{listing_id}",
        "title": "2-комнатная квартира",
        "address": "Алматы",
        "actual_price": 50_000_000,
        "fair_price": 48_000_000.0,
        "verdict": "FAIR",
        "diff_pct": 4.2,
        "top_factors": [],
        "photos": [],
        "price_history": [],
        "duplicate_of": None,
        "analogs": [],
        "scam_risk": None,
        "renovation": None,
    }


def test_cached_answer_is_served_even_past_the_rate_limit(monkeypatch):
    """CGNAT: сотни живых людей с одного IP. Отдать им готовый ответ из памяти
    стоит ноль, поэтому лимит проверяется ПОСЛЕ попадания в кэш."""
    monkeypatch.setattr(
        predict_gate, "predict_from_url", lambda url, live_vision=False, timeout=None: _payload()
    )
    client = TestClient(app)
    url = "https://krisha.kz/a/show/91"
    assert client.post("/api/predict", json={"url": url}).status_code == 200

    # выбираем весь бюджет лимита другими объявлениями
    for i in range(app_module.RATE_LIMIT + 5):
        client.post("/api/predict", json={"url": f"https://krisha.kz/a/show/{1000 + i}"})
    assert (
        client.post("/api/predict", json={"url": "https://krisha.kz/a/show/777"}).status_code == 429
    )

    # а тот самый лот из поста по-прежнему отдаётся
    resp = client.post("/api/predict", json={"url": url + "?utm_source=telegram"})
    assert resp.status_code == 200
    assert resp.json()["listing_id"] == 91


def test_failed_listing_is_negatively_cached(monkeypatch):
    """Битая ссылка в посте не должна гнать каждого посетителя в полный
    скрейп-цикл: минуту помним, что не получилось."""
    attempts: list[int] = []

    def boom(url, live_vision=False, timeout=None):
        attempts.append(1)
        raise RuntimeError("krisha не ответила")

    monkeypatch.setattr(predict_gate, "predict_from_url", boom)
    client = TestClient(app)
    url = "https://krisha.kz/a/show/91"

    assert client.post("/api/predict", json={"url": url}).status_code == 502
    assert client.post("/api/predict", json={"url": url}).status_code == 502
    assert len(attempts) == 1, "второй запрос не должен снова ходить на krisha.kz"

    # ...но это ненадолго: после протухания негативного кэша пробуем снова
    predict_gate._negative.clear()
    assert client.post("/api/predict", json={"url": url}).status_code == 502
    assert len(attempts) == 2


def test_busy_service_answers_503_instead_of_hanging(monkeypatch):
    """Слотов нет — быстрый отказ с Retry-After, а не висящий спиннер."""
    monkeypatch.setattr(predict_gate, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(predict_gate, "PREDICT_SLOT_WAIT_S", 0.05)
    monkeypatch.setattr(
        predict_gate, "predict_from_url", lambda url, live_vision=False, timeout=None: _payload()
    )
    predict_gate._slots.acquire()  # занят кем-то другим
    try:
        resp = TestClient(app).post("/api/predict", json={"url": "https://krisha.kz/a/show/91"})
    finally:
        predict_gate._slots.release()

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "30"


def test_user_path_uses_short_network_budget(monkeypatch):
    """Краулерные 30 секунд на пользовательском пути — это минута worst-case
    на один запрос и десять забитых слотов."""
    seen: list[httpx.Timeout] = []

    def capture(url, live_vision=False, timeout=None):
        seen.append(timeout)
        return _payload()

    monkeypatch.setattr(predict_gate, "predict_from_url", capture)
    TestClient(app).post("/api/predict", json={"url": "https://krisha.kz/a/show/91"})

    assert isinstance(seen[0], httpx.Timeout)
    assert seen[0].read == pytest.approx(predict_gate.USER_SCRAPE_TIMEOUT_S)
    assert seen[0].connect == pytest.approx(predict_gate.USER_SCRAPE_CONNECT_S)


def test_bot_shares_the_same_gate(monkeypatch):
    """Раньше бот ходил в predict_from_url напрямую — мимо кэша и слотов."""
    calls: list[str] = []

    def fake(url, live_vision=True, timeout=None):
        calls.append(url)
        return _payload(123)

    monkeypatch.setattr(predict_gate, "predict_from_url", fake)
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: {"ok": True})

    for _ in range(3):
        bot.handle_update(
            {"message": {"chat": {"id": 42}, "text": "https://krisha.kz/a/show/123"}}
        )

    assert len(calls) == 1


def test_bot_tells_the_user_when_the_service_is_busy(monkeypatch):
    monkeypatch.setattr(predict_gate, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(predict_gate, "PREDICT_SLOT_WAIT_S", 0.05)
    monkeypatch.setattr(
        predict_gate, "predict_from_url", lambda url, live_vision=True, timeout=None: _payload()
    )
    sent: list[dict] = []
    monkeypatch.setattr(
        bot, "tg_call", lambda method, **kw: sent.append({"method": method, **kw}) or {"ok": True}
    )

    predict_gate._slots.acquire()
    try:
        bot.handle_update({"message": {"chat": {"id": 42}, "text": "https://krisha.kz/a/show/1"}})
    finally:
        predict_gate._slots.release()

    texts = [m.get("text", "") for m in sent]
    assert any("много запросов" in t for t in texts)


def test_bot_and_web_results_do_not_mix(monkeypatch):
    """У бота живой Vision, у веба — нет: это разные разборы одного лота."""
    modes: list[bool] = []

    def fake(url, live_vision=False, timeout=None):
        modes.append(live_vision)
        return _payload(5)

    monkeypatch.setattr(predict_gate, "predict_from_url", fake)
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: {"ok": True})

    TestClient(app).post("/api/predict", json={"url": "https://krisha.kz/a/show/5"})
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "https://krisha.kz/a/show/5"}})

    assert modes == [False, True]


def test_single_flight_survives_the_crowd(monkeypatch):
    """Восемь параллельных запросов одного лота — один поход на krisha.kz."""
    calls: list[str] = []

    def slow(url, live_vision=False, timeout=None):
        calls.append(url)
        time.sleep(0.05)
        return _payload()

    monkeypatch.setattr(predict_gate, "predict_from_url", slow)
    monkeypatch.setattr(app_module, "RATE_LIMIT", 10_000)
    client = TestClient(app)

    def hit():
        assert (
            client.post(
                "/api/predict", json={"url": "https://krisha.kz/a/show/91"}
            ).status_code
            == 200
        )

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1


# ------------------------------------------------------------------ лимиты
def test_demo_is_not_starved_by_the_predict_limit(monkeypatch):
    """Демо дёргает каждая загрузка главной; строгий лимит предикта его не касается."""
    monkeypatch.setattr(
        predict_gate, "predict_from_url", lambda url, live_vision=False, timeout=None: _payload()
    )
    client = TestClient(app)
    for i in range(app_module.RATE_LIMIT + 3):
        client.post("/api/predict", json={"url": f"https://krisha.kz/a/show/{2000 + i}"})

    resp = client.get("/api/demo")

    assert resp.status_code != 429


def test_demo_still_has_its_own_ceiling(monkeypatch):
    monkeypatch.setattr(app_module, "DEMO_RATE_LIMIT", 3)
    client = TestClient(app)
    codes = [client.get("/api/demo").status_code for _ in range(5)]

    assert 429 in codes


# ------------------------------------------------------------------ метрики
def test_metrics_endpoint_reports_traffic(monkeypatch):
    monkeypatch.setattr(
        predict_gate, "predict_from_url", lambda url, live_vision=False, timeout=None: _payload()
    )
    client = TestClient(app)
    client.get("/")
    client.post("/api/predict", json={"url": "https://krisha.kz/a/show/91"})
    client.post("/api/predict", json={"url": "https://krisha.kz/a/show/91"})

    data = client.get("/api/metrics").json()

    assert data["requests"] >= 3
    assert data["counters"]["predict_cache_hit"] == 1
    assert data["statuses"]["2xx"] >= 3
    assert data["routes"]["GET /"]["count"] == 1
    assert data["routes"]["GET /"]["p95_ms"] >= 0
    assert data["predict"]["limiter_total"] == 10
    assert data["assets"]["precompressed"] >= 4
