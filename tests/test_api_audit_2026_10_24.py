"""Аудит 24.10, раздел 4 (API): регрессии на каждую находку. Без сети, быстрые."""

import asyncio
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from krisha import predict_gate
from krisha.api import app as app_module
from krisha.api.app import app
from krisha.api.cache import TTLCache
from krisha.predict import InvalidListingUrl, ListingNotFound
from krisha.scraping.client import ChallengeBlocked, SourceUnavailable

PROXY_KEY = "k" * 40


def _proxied_request(ip: str) -> Request:
    headers = [(b"x-bagam-proxy-key", PROXY_KEY.encode()), (b"x-bagam-client-ip", ip.encode())]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/predict",
            "headers": headers,
            "client": ("104.16.0.1", 1),
            "query_string": b"",
        }
    )


def _tb_depth(exc: BaseException) -> int:
    n, tb = 0, exc.__traceback__
    while tb is not None:
        n += 1
        tb = tb.tb_next
    return n


# --------------------------------------------------------------------------
# 1. Rate-limit: IPv6 сворачивается до /64, словарь ключей ограничен
# --------------------------------------------------------------------------
def test_rate_subject_folds_ipv6_to_64_and_unmaps_ipv4():
    subject = app_module._rate_subject
    assert subject("2001:db8:1:2::1") == subject("2001:db8:1:2:ffff:ffff:ffff:ffff") == "2001:db8:1:2::/64"
    assert subject("2001:db8:1:3::1") != subject("2001:db8:1:2::1")
    assert subject("::ffff:203.0.113.7") == "203.0.113.7"
    assert subject("203.0.113.7") == "203.0.113.7"
    assert subject("fe80::1%eth0") == "fe80::/64"
    assert subject("?") == "?"


def test_rotating_ipv6_inside_64_does_not_bypass_limit(monkeypatch):
    """Аудит: 200 разных адресов одной /64 проходили все 200 при лимите 15."""
    monkeypatch.setenv("PROXY_KEY", PROXY_KEY)
    app_module._rate.clear()
    ok = blocked = 0
    for i in range(200):
        try:
            app_module._check_rate_limit(_proxied_request(f"2001:db8:1:2::{i:x}"))
            ok += 1
        except HTTPException as exc:
            assert exc.status_code == 429
            blocked += 1
    assert ok == app_module.RATE_LIMIT
    assert blocked == 200 - app_module.RATE_LIMIT
    # соседняя /64 — другой абонент, у него свой бюджет
    app_module._check_rate_limit(_proxied_request("2001:db8:1:3::1"))


def test_rate_table_has_hard_cap(monkeypatch):
    """Поток уникальных ключей вытесняет самые давние, а не растит словарь."""
    monkeypatch.setenv("PROXY_KEY", PROXY_KEY)
    monkeypatch.setattr(app_module, "MAX_RATE_KEYS", 50)
    app_module._rate.clear()
    for i in range(500):
        app_module._check_rate_limit(_proxied_request(f"203.0.{i // 250}.{i % 250}"))
    assert len(app_module._rate) == 50
    # остались самые свежие
    assert "api:203.0.1.249" in app_module._rate
    assert "api:203.0.0.0" not in app_module._rate


def test_rate_table_drops_expired_keys(monkeypatch):
    monkeypatch.setenv("PROXY_KEY", PROXY_KEY)
    app_module._rate.clear()
    for i in range(20):
        app_module._check_rate_limit(_proxied_request(f"198.51.100.{i}"))
    # «отматываем» время: все записи старше окна
    for dq in app_module._rate.values():
        for j in range(len(dq)):
            dq[j] -= app_module.RATE_WINDOW_S + 1
    app_module._check_rate_limit(_proxied_request("198.51.100.200"))
    assert list(app_module._rate) == ["api:198.51.100.200"]


# --------------------------------------------------------------------------
# 2. Отказ источника: один на всех ждущих, коротко
# --------------------------------------------------------------------------
@pytest.mark.parametrize("exc_type", [SourceUnavailable, ChallengeBlocked])
def test_source_failure_answers_every_waiter_with_one_scrape(monkeypatch, exc_type):
    """Аудит: 8 запросов на одну ссылку → 8 скрейпов по очереди."""
    calls: list[int] = []

    def slow_fail(url, live_vision=False, timeout=None, on_attempt=None):
        calls.append(1)
        time.sleep(0.2)
        raise exc_type("источник лежит")

    monkeypatch.setattr(predict_gate, "predict_from_url", slow_fail)
    errors: list[BaseException] = []

    def go():
        try:
            predict_gate.cached_predict("https://krisha.kz/a/show/777")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert len(errors) == 10 and all(isinstance(e, exc_type) for e in errors)


def test_waiters_share_failure_even_after_short_ttl(monkeypatch):
    """Отказ, случившийся пока поток ждал лок, отвечает и ему — даже если
    короткий TTL уже истёк (скрейп шёл дольше, чем живёт запись)."""
    calls: list[int] = []

    def slow_fail(url, live_vision=False, timeout=None, on_attempt=None):
        calls.append(1)
        time.sleep(0.2)
        raise SourceUnavailable("таймаут")

    monkeypatch.setattr(predict_gate, "predict_from_url", slow_fail)
    monkeypatch.setattr(predict_gate, "SOURCE_FAILURE_TTL_S", 0.0)

    def go():
        try:
            predict_gate.cached_predict("https://krisha.kz/a/show/778")
        except SourceUnavailable:
            pass

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    # новый запрос после истечения TTL снова идёт наружу: krisha могла ожить
    with pytest.raises(SourceUnavailable):
        predict_gate.cached_predict("https://krisha.kz/a/show/778")
    assert len(calls) == 2


def test_cached_source_failure_does_not_extend_its_own_ttl(monkeypatch):
    """Копия из кэша не перезаписывает метку: иначе под трафиком «лежит» вечно."""
    calls: list[int] = []

    def fail(url, live_vision=False, timeout=None, on_attempt=None):
        calls.append(1)
        raise SourceUnavailable("таймаут")

    monkeypatch.setattr(predict_gate, "predict_from_url", fail)
    monkeypatch.setattr(predict_gate, "SOURCE_FAILURE_TTL_S", 0.3)
    url = "https://krisha.kz/a/show/779"
    with pytest.raises(SourceUnavailable):
        predict_gate.cached_predict(url)
    deadline = time.monotonic() + 0.45
    while time.monotonic() < deadline:
        with pytest.raises(SourceUnavailable):
            predict_gate.cached_predict(url)
        time.sleep(0.02)
    assert len(calls) == 2, "через TTL после ПЕРВОГО отказа — новая попытка"


def test_abandoned_predict_threads_stop_hitting_source(monkeypatch):
    """Аудит: после 503 по таймауту брошенные потоки продолжали скрейпить по очереди."""
    import httpx

    calls: list[int] = []

    def slow_fail(url, live_vision=False, timeout=None, on_attempt=None):
        calls.append(1)
        time.sleep(0.3)
        raise SourceUnavailable("down")

    monkeypatch.setattr(predict_gate, "predict_from_url", slow_fail)
    monkeypatch.setattr(app_module, "PREDICT_WAIT_S", 0.1)
    monkeypatch.setattr(app_module.usage, "record_event", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_check_rate_limit", lambda *a, **k: None)

    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            responses = await asyncio.gather(
                *[client.post("/api/predict", json={"url": "https://krisha.kz/a/show/42"}) for _ in range(6)]
            )
        assert all(r.status_code == 503 for r in responses)
        await asyncio.sleep(0.8)

    asyncio.run(main())
    assert len(calls) == 1


# --------------------------------------------------------------------------
# 3. Локи не текут; мусорный URL — 422 до кэша и слотов
# --------------------------------------------------------------------------
def test_failed_producer_does_not_leak_locks():
    cache = TTLCache(ttl=60)
    for i in range(100):
        with pytest.raises(RuntimeError):
            cache.get_or_call(i, lambda: (_ for _ in ()).throw(RuntimeError("нет")))
    assert cache._locks == {}
    cache.get_or_call("ok", lambda: 1)
    assert cache._locks == {}


def test_lock_stays_while_someone_holds_or_waits_for_it():
    """Лок нельзя выкинуть, пока его держит/ждёт другой поток: иначе новый
    поток завёл бы второй лок и пересчёт пошёл бы параллельно."""
    cache = TTLCache(ttl=60)
    started, release = threading.Event(), threading.Event()
    calls: list[int] = []

    def producer():
        calls.append(1)
        started.set()
        release.wait(5)
        raise RuntimeError("упал")

    def call():
        try:
            cache.get_or_call("k", producer)
        except RuntimeError:
            pass

    first = threading.Thread(target=call)
    first.start()
    started.wait(5)
    second = threading.Thread(target=call)
    second.start()
    for _ in range(100):
        if cache._locks["k"].users == 2:
            break
        time.sleep(0.01)
    assert cache._locks["k"].users == 2
    started.clear()
    release.set()
    first.join(5)
    second.join(5)
    assert cache._locks == {}
    assert len(calls) == 2  # ошибки не кэшируются: второй попробовал сам, но ПОСЛЕ первого


def test_garbage_url_is_rejected_before_cache_and_slots(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(predict_gate, "predict_from_url", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(predict_gate, "_slots", threading.BoundedSemaphore(1))
    predict_gate._slots.acquire()  # все слоты заняты
    try:
        for i in range(50):
            with pytest.raises(InvalidListingUrl):
                predict_gate.cached_predict(f"garbage-{i}", wait_s=5)
        resp = TestClient(app).post("/api/predict", json={"url": "https://example.com/x"})
    finally:
        predict_gate._slots.release()
    assert resp.status_code == 422, "мусор — ошибка ввода, а не «занято»"
    assert calls == []
    assert predict_gate._cache._locks == {}
    assert len(predict_gate._negative) == 0


def test_listing_failures_leave_no_locks(monkeypatch):
    def nf(url, live_vision=False, timeout=None, on_attempt=None):
        raise ListingNotFound("нет")

    monkeypatch.setattr(predict_gate, "predict_from_url", nf)
    for i in range(200):
        with pytest.raises(ListingNotFound):
            predict_gate.cached_predict(f"https://krisha.kz/a/show/{i}")
    assert predict_gate._cache._locks == {}


# --------------------------------------------------------------------------
# 4. Traceback закэшированного исключения не растёт
# --------------------------------------------------------------------------
def test_cached_exception_traceback_does_not_grow(monkeypatch):
    def nf(url, live_vision=False, timeout=None, on_attempt=None):
        raise ListingNotFound("нет")

    monkeypatch.setattr(predict_gate, "predict_from_url", nf)
    raised: list[BaseException] = []
    for _ in range(200):
        try:
            predict_gate.cached_predict("https://krisha.kz/a/show/1")
        except ListingNotFound as exc:
            raised.append(exc)
    assert max(_tb_depth(e) for e in raised[1:]) < 5
    stored = predict_gate._negative.peek(predict_gate.cache_key("https://krisha.kz/a/show/1"))[1]
    assert stored.__traceback__ is None
    assert str(raised[-1]) == "нет"


# --------------------------------------------------------------------------
# 5. Бот — через свой лимитер; кэш-хит — без тредпула
# --------------------------------------------------------------------------
def test_bot_update_runs_under_its_own_limiter(monkeypatch):
    monkeypatch.setattr(app_module.bot, "bot_token", lambda: "test-token")
    seen: list[int] = []
    monkeypatch.setattr(
        app_module.bot,
        "handle_update",
        lambda update: seen.append(app_module._BOT_LIMITER.statistics().borrowed_tokens),
    )
    secret = app_module.bot.webhook_secret("test-token")
    resp = TestClient(app).post(
        "/tg/webhook", json={"message": {"text": "hi"}}, headers={"X-Telegram-Bot-Api-Secret-Token": secret}
    )
    assert resp.status_code == 200
    assert seen == [1]
    assert app_module._BOT_LIMITER.statistics().borrowed_tokens == 0


def test_cache_hit_records_usage_without_threadpool(monkeypatch):
    monkeypatch.setattr(
        predict_gate,
        "predict_from_url",
        lambda url, live_vision=False, timeout=None, on_attempt=None: {
            "listing_id": 91,
            "url": "https://krisha.kz/a/show/91",
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
        },
    )
    in_loop: list[bool] = []

    def record(kind, *a, **k):
        try:
            asyncio.get_running_loop()
            in_loop.append(True)
        except RuntimeError:
            in_loop.append(False)

    monkeypatch.setattr(app_module.usage, "record_event", record)
    client = TestClient(app)
    url = "https://krisha.kz/a/show/91"
    assert client.post("/api/predict", json={"url": url}).status_code == 200
    assert client.post("/api/predict", json={"url": url}).status_code == 200
    assert in_loop == [True, True], "record_event зовётся прямо в event loop, без похода в тредпул"


# --------------------------------------------------------------------------
# 6. Stale-while-revalidate для stats/heatmap/rent
# --------------------------------------------------------------------------
def test_stale_value_is_served_while_one_thread_refreshes():
    cache = TTLCache(ttl=0.05, stale_ttl=60, serve_stale_while_refreshing=True)
    cache.get_or_call("stats", lambda: "старое")
    time.sleep(0.06)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "новое"

    result: list[str] = []
    refresher = threading.Thread(target=lambda: result.append(cache.get_or_call("stats", slow)))
    refresher.start()
    started.wait(5)
    t0 = time.monotonic()
    readers = [cache.get_or_call("stats", lambda: pytest.fail("второй пересчёт")) for _ in range(20)]
    assert time.monotonic() - t0 < 0.5, "читатели не ждут чужой пересчёт"
    assert readers == ["старое"] * 20
    release.set()
    refresher.join(5)
    assert result == ["новое"]
    assert cache.get_or_call("stats", lambda: "лишнее") == "новое"
    assert cache._locks == {}


def test_without_stale_value_readers_wait_for_single_flight():
    """Пустой кэш (рестарт без прогрева): отдавать нечего — ждём один пересчёт."""
    cache = TTLCache(ttl=60, stale_ttl=60, serve_stale_while_refreshing=True)
    calls: list[int] = []

    def slow():
        calls.append(1)
        time.sleep(0.1)
        return "значение"

    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(cache.get_or_call("k", slow))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1]
    assert results == ["значение"] * 8


def test_stats_heatmap_rent_caches_serve_stale():
    for cache in (app_module._stats_cache, app_module._heatmap_cache, app_module._rent_stats_cache):
        assert cache.serve_stale_while_refreshing
        assert cache.stale_ttl
