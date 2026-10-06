"""Кэш разбора Gemini в text_parse: повтор текста не тратит квоту ключа.

Аудит безопасности 2026-10-06: любой текст от 40 символов шёл в живой Gemini
без кэша — участник чата мог выжечь дневную квоту, слав боту одно и то же.
"""

import json
import threading

import httpx
import pytest

from krisha import text_parse
from krisha.text_parse import _cache_key, predict_from_text

TEXT = ("Продам уютную 2-комнатную квартиру 60 м² в Бостандыкском районе, "
        "5/9 этаж, кирпичный дом 2015 года, 45 млн тенге, торг.")

PARSED = {"is_listing": True, "rooms": 2, "area": 60.0, "floor": 5,
          "total_floors": 9, "year_built": 2015, "district": "Бостандыкский",
          "building_type": "кирпичный", "price": 45_000_000}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    text_parse._parse_cache.clear()
    yield
    text_parse._parse_cache.clear()


class FakeResponse:
    def __init__(self, parsed=None, status_code=200):
        self.status_code = status_code
        self._parsed = parsed
        self.text = json.dumps(parsed) if parsed is not None else "error"

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(self._parsed)}]}}]}


@pytest.fixture
def gemini(monkeypatch):
    """Подменяет httpx.post: считает вызовы, ответы берёт из очереди (последний повторяется)."""
    state = {"calls": [], "queue": [FakeResponse(PARSED)]}

    def fake_post(url, json=None, headers=None, timeout=None):
        state["calls"].append(json)
        item = state["queue"][0] if len(state["queue"]) == 1 else state["queue"].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(
        "krisha.predict.predict_from_listing",
        lambda listing, live_vision=True: {
            "fair_price": 48_000_000, "verdict": "FAIR", "actual_price": listing.get("price")},
    )
    return state


def test_second_call_does_not_hit_the_network(gemini):
    first = predict_from_text(TEXT)
    second = predict_from_text(TEXT)

    assert len(gemini["calls"]) == 1
    assert first["from_text"] is True and second["from_text"] is True
    assert first["fair_price"] == second["fair_price"] == 48_000_000
    assert first["parsed_fields"] == second["parsed_fields"]


def test_whitespace_variants_share_one_entry_but_other_text_does_not(gemini):
    predict_from_text(TEXT)
    predict_from_text("  " + TEXT.replace(" ", "   ").replace(",", ",\n") + "\n\n")
    assert len(gemini["calls"]) == 1

    predict_from_text(TEXT + " Срочно!")
    assert len(gemini["calls"]) == 2


def test_not_a_listing_is_cached_too(gemini):
    gemini["queue"] = [FakeResponse({"is_listing": False})]
    assert predict_from_text(TEXT) is None
    assert predict_from_text(TEXT) is None
    assert len(gemini["calls"]) == 1


def test_missing_key_fields_answer_is_cached_and_not_shared_by_reference(gemini):
    gemini["queue"] = [FakeResponse({"is_listing": True, "rooms": None, "area": None})]
    first = predict_from_text(TEXT)
    assert first["error"] == "no_key_fields"
    first["parsed"]["area"] = 999  # вызывающий код не должен портить кэш

    second = predict_from_text(TEXT)
    assert len(gemini["calls"]) == 1
    assert second["error"] == "no_key_fields" and second["parsed"]["area"] is None


@pytest.mark.parametrize("failure", [
    FakeResponse(status_code=500),
    FakeResponse(status_code=429),
    httpx.ConnectError("нет сети"),
])
def test_failures_are_not_cached(gemini, failure):
    """Сбой (сеть, квота) временный: повтор через минуту не должен час получать отказ."""
    gemini["queue"] = [failure, FakeResponse(PARSED)]
    assert predict_from_text(TEXT) is None
    assert len(text_parse._parse_cache) == 0

    result = predict_from_text(TEXT)  # Gemini ожил — разбор проходит
    assert result["from_text"] is True
    assert len(gemini["calls"]) == 2

    predict_from_text(TEXT)  # а вот успех уже закэширован
    assert len(gemini["calls"]) == 2


def test_entry_expires_after_ttl(gemini):
    predict_from_text(TEXT)
    cache = text_parse._parse_cache
    assert cache.ttl == 3600.0

    predict_from_text(TEXT)
    assert len(gemini["calls"]) == 1

    for key, (stamp, value) in list(cache._values.items()):
        cache._values[key] = (stamp - cache.ttl - 1, value)  # состарили запись
    predict_from_text(TEXT)
    assert len(gemini["calls"]) == 2


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(text_parse, "_gemini_extract", lambda text, key: {"is_listing": False})
    assert text_parse._parse_cache.maxsize == 512
    for i in range(700):
        text_parse._extract_cached(f"{TEXT} вариант {i}", "k")
    assert len(text_parse._parse_cache) == 512


def test_cache_key_is_a_hash_of_normalized_text():
    key = _cache_key("  Продам   квартиру\n\nв Алматы\t торг ")
    assert key == _cache_key("Продам квартиру в Алматы торг")
    assert len(key) == 64 and int(key, 16) >= 0  # sha256 hex
    assert "Продам" not in key

    # в ключ входят первые 3000 символов, хвост за ними на него не влияет
    base = "а" * 3000
    assert _cache_key(base + " хвост 1") == _cache_key(base + " хвост 2")
    assert _cache_key(base[:-1] + "б") != _cache_key(base)
    assert _cache_key("раз два") != _cache_key("раздва")


def test_cache_does_not_keep_the_raw_text(gemini):
    secret = TEXT + " Звоните +7 777 123 45 67, Айгуль."
    predict_from_text(secret)

    cache = text_parse._parse_cache
    assert len(cache) == 1
    assert all(len(key) == 64 for key in cache._values)
    assert "Айгуль" not in repr(cache._values) and "777" not in repr(cache._values)


def test_no_key_or_short_text_never_touches_cache_or_network(gemini, monkeypatch):
    assert predict_from_text("привет") is None
    monkeypatch.delenv("GEMINI_API_KEY")
    assert predict_from_text(TEXT) is None
    assert gemini["calls"] == [] and len(text_parse._parse_cache) == 0


def test_cache_is_thread_safe(monkeypatch):
    calls = []

    def fake_extract(text, key):
        calls.append(text)
        return {"is_listing": False}

    monkeypatch.setattr(text_parse, "_gemini_extract", fake_extract)
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(n):
        try:
            barrier.wait()
            for i in range(200):
                text_parse._extract_cached(f"{TEXT} #{(n * 7 + i) % 40}", "k")
        except BaseException as exc:  # noqa: BLE001 — тест собирает любые сбои потоков
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(text_parse._parse_cache) == 40
    assert 40 <= len(calls) <= 40 * 8  # гонка допускает дубли вызовов, но не потерю кэша
