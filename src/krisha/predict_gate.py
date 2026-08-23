"""Единая калитка к тяжёлому предикту: кэш, single-flight, слоты, короткий бюджет.

Сценарий, ради которого это существует: ссылку на сервис постят в большой
телеграм-канал. За две минуты приходит тысяча человек, и почти все проверяют
ОДНО И ТО ЖЕ объявление — то, что в посте. Без калитки это тысяча походов на
krisha.kz, тысяча инференсов CatBoost и тысяча занятых потоков.

Три предохранителя:

1. **Кэш по id объявления** (``PREDICT_CACHE_TTL_S``). Один разбор на всех.
2. **Single-flight.** Пока первый разбор идёт, остальные ждут ЕГО результат, а
   не запускают свой (per-key lock у ``TTLCache``).
3. **Негативный кэш** (``PREDICT_NEGATIVE_TTL_S``). Битая или удалённая ссылка
   в посте иначе означает, что каждый посетитель заново гоняет полный цикл
   скрейпа с ретраями — самый дорогой из возможных путей.

Плюс общий на процесс лимит одновременных скрейпов (``PREDICT_SLOTS``) с
ОГРАНИЧЕННЫМ ожиданием: лучше быстро ответить «занято, попробуй через
полминуты», чем держать сотню висящих спиннеров и скрейпить для людей,
которые уже закрыли вкладку.

Модуль общий для веба и бота специально. Раньше бот ходил в
``predict_from_url`` напрямую, мимо кэша и мимо лимитера: наплыв в бота
(пост-то и про бота) съедал потоки в обход всех предохранителей веба.

Состояние — в памяти процесса. При ``WEB_CONCURRENCY=2`` калиток две, то есть
фактические лимиты удваиваются; это учтено в размере слотов.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import httpx

from krisha.api.cache import TTLCache
from krisha.predict import KRISHA_URL_RE, InvalidListingUrl, predict_from_url

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


PREDICT_CACHE_TTL_S = _env_float("PREDICT_CACHE_TTL_S", 600.0)
# Короче позитивного: объявление могли починить/вернуть, и держать «не
# получилось» десять минут было бы обидно. Но и повторять скрейп на каждый
# запрос нельзя — минута тишины по битой ссылке.
PREDICT_NEGATIVE_TTL_S = _env_float("PREDICT_NEGATIVE_TTL_S", 60.0)
PREDICT_SLOTS = max(1, _env_int("PREDICT_SLOTS", 10))
# Сколько ждать свободный слот, прежде чем отказать. 15 с — верхняя граница
# терпения человека, который смотрит на спиннер.
PREDICT_SLOT_WAIT_S = _env_float("PREDICT_SLOT_WAIT_S", 15.0)
# Бюджет сети на пользовательский скрейп. Краулерные 30 с здесь неуместны:
# при подвисшем коннекте два ретрая давали worst-case за минуту на ОДИН
# запрос, и десять таких намертво забивали слоты.
USER_SCRAPE_TIMEOUT_S = _env_float("USER_SCRAPE_TIMEOUT_S", 5.0)
USER_SCRAPE_CONNECT_S = _env_float("USER_SCRAPE_CONNECT_S", 3.0)


class PredictBusy(RuntimeError):
    """Все слоты заняты дольше отведённого ожидания."""


_cache = TTLCache(ttl=PREDICT_CACHE_TTL_S, maxsize=2048)
_negative: TTLCache = TTLCache(ttl=PREDICT_NEGATIVE_TTL_S, maxsize=2048)
_slots = threading.BoundedSemaphore(PREDICT_SLOTS)
_busy_count = 0
_busy_lock = threading.Lock()


def user_timeout() -> httpx.Timeout:
    """Таймаут httpx для пользовательского пути (веб и бот)."""
    return httpx.Timeout(USER_SCRAPE_TIMEOUT_S, connect=USER_SCRAPE_CONNECT_S)


def cache_key(url: str, live_vision: bool = False) -> str:
    """Ключ = id объявления (+ признак vision).

    Один лот открывают по разным ссылкам (utm-хвосты, http/https,
    /a/show/ID?query) — это один и тот же разбор. А вот разбор с живым
    Gemini Vision (путь бота) и без него — разные результаты, поэтому
    смешивать их в одном ключе нельзя.
    """
    match = KRISHA_URL_RE.search(url or "")
    base = match.group(1) if match else (url or "").strip()[:200]
    return f"{base}:v" if live_vision else base


def peek(url: str, live_vision: bool = False) -> dict[str, Any] | None:
    """Готовый разбор из кэша или None. Без единого похода наружу."""
    entry = _cache.peek(cache_key(url, live_vision))
    if entry is not None and entry[0] < PREDICT_CACHE_TTL_S:
        return entry[1]
    return None


def peek_failure(url: str, live_vision: bool = False) -> Exception | None:
    """Свежая ошибка по этому объявлению (негативный кэш) или None."""
    entry = _negative.peek(cache_key(url, live_vision))
    if entry is not None and entry[0] < PREDICT_NEGATIVE_TTL_S:
        return entry[1]
    return None


def stats() -> dict[str, int]:
    return {
        "cached": len(_cache),
        "negative": len(_negative),
        "slots": PREDICT_SLOTS,
        "busy": _busy_count,
    }


def clear() -> None:
    """Только для тестов."""
    global _busy_count
    _cache.clear()
    _negative.clear()
    with _busy_lock:
        _busy_count = 0


def _run(url: str, live_vision: bool, wait_s: float) -> dict[str, Any]:
    global _busy_count
    if not _slots.acquire(timeout=max(0.0, wait_s)):
        raise PredictBusy("Сервис перегружен, попробуйте через полминуты")
    with _busy_lock:
        _busy_count += 1
    try:
        return predict_from_url(url, live_vision=live_vision, timeout=user_timeout())
    finally:
        with _busy_lock:
            _busy_count -= 1
        _slots.release()


def cached_predict(
    url: str, *, live_vision: bool = False, wait_s: float | None = None
) -> dict[str, Any]:
    """Разбор объявления через кэш, single-flight и слоты.

    Поднимает ``PredictBusy``, если слот не освободился за ``wait_s``, и
    прокидывает ошибки самого разбора (запомнив их в негативном кэше).
    ``InvalidListingUrl`` не кэшируется: это ошибка ввода, скрейпа не было.
    """
    key = cache_key(url, live_vision)
    fresh = _cache.peek(key)
    if fresh is not None and fresh[0] < PREDICT_CACHE_TTL_S:
        return fresh[1]
    failure = peek_failure(url, live_vision)
    if failure is not None:
        raise failure
    wait = PREDICT_SLOT_WAIT_S if wait_s is None else wait_s
    try:
        return _cache.get_or_call(key, lambda: _run(url, live_vision, wait))
    except (PredictBusy, InvalidListingUrl):
        # Занятость — состояние сервиса, а не свойство объявления; кривой URL —
        # ошибка ввода. Ни то, ни другое в негативный кэш не кладём.
        raise
    except Exception as exc:
        _negative.set(key, exc)
        raise
