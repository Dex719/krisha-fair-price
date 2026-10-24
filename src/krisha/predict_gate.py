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

import copy
import logging
import os
import threading
import time
from typing import Any

import httpx

from krisha.api import metrics
from krisha.api.cache import TTLCache
from krisha.predict import KRISHA_URL_RE, InvalidListingUrl, predict_from_url, user_client
from krisha.scraping.client import SourceUnavailable

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
# Отказ ИСТОЧНИКА (SafeLine, таймауты, 5xx) — состояние чужого сервера, а не
# объявления: держим его секунды, а не минуту, — krisha может ожить. Но без
# этих секунд восемь человек с одной ссылкой давали восемь скрейпов подряд,
# пять из них — уже после того, как клиентам ответили 503 (аудит 24.10).
SOURCE_FAILURE_TTL_S = _env_float("SOURCE_FAILURE_TTL_S", 4.0)
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
# Отказы источника: ключ → (time.perf_counter() отказа, исключение без traceback).
# perf_counter, а не monotonic: на Windows у monotonic шаг ~16 мс, и «отказ
# случился, пока я ждал» неотличим от «отказ был до меня».
_source_failures: TTLCache = TTLCache(ttl=SOURCE_FAILURE_TTL_S, maxsize=2048)
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
    return _fresh_failure(cache_key(url, live_vision))


def _detached(exc: Exception, *, may_reset: bool = True) -> Exception:
    """Копия исключения без traceback, __cause__ и __context__.

    Закэшированное исключение бросают снова и снова. ``raise failure`` на том
    же объекте каждый раз ДОПИСЫВАЕТ кадры к его __traceback__ (аудит 24.10:
    глубина 404 после 200 попаданий) и держит в памяти все эти кадры с их
    локальными переменными. Поэтому в кэш кладём и наружу бросаем копию.

    Если копию не собрать (экзотический ``__init__``), при ``may_reset``
    обрезаем traceback у самого объекта — так можно с тем, что лежит в кэше,
    но не с исключением, которое прямо сейчас летит к вызывающему.
    """
    try:
        clone = copy.copy(exc)
        ok = type(clone) is type(exc) and clone.args == exc.args
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        return exc.with_traceback(None) if may_reset else exc
    clone.__traceback__ = None
    clone.__cause__ = None
    clone.__context__ = None
    return clone


def _remember(cache: TTLCache, key: str, exc: Exception, value_of=lambda stored: stored) -> None:
    """Кладёт отказ в кэш, если он не из этого же кэша.

    Копия, брошенная из кэша, помечена: перезаписать ею запись значило бы
    продлевать короткий TTL отказа источника с каждым запросом — и krisha,
    уже ожившая, оставалась бы «лежащей», пока идёт трафик.
    """
    if getattr(exc, "_from_gate_cache", False):
        return
    stored = _detached(exc, may_reset=False)
    try:
        stored._from_gate_cache = True  # type: ignore[attr-defined]
    except AttributeError:  # __slots__ без __dict__ — просто не помечаем
        pass
    cache.set(key, value_of(stored))


def _fresh_failure(key: str, since: float | None = None) -> Exception | None:
    """Ошибка по ключу, которую надо отдать вместо нового скрейпа.

    Негативный кэш объявления (минута) или свежий отказ источника (секунды).
    ``since`` — когда поток встал в очередь: отказ, случившийся ПОКА он ждал
    лок, отвечает и ему, даже если короткий TTL уже истёк — повторять за
    соседом тот же скрейп незачем.
    """
    entry = _negative.peek(key)
    if entry is not None and entry[0] < PREDICT_NEGATIVE_TTL_S:
        return _detached(entry[1])
    entry = _source_failures.peek(key)
    if entry is not None:
        age, (failed_at, exc) = entry
        if age < SOURCE_FAILURE_TTL_S or (since is not None and failed_at > since):
            return _detached(exc)
    return None


def stats() -> dict[str, int]:
    return {
        "cached": len(_cache),
        "negative": len(_negative),
        "source_failures": len(_source_failures),
        "slots": PREDICT_SLOTS,
        "busy": _busy_count,
    }


def clear() -> None:
    """Только для тестов."""
    global _busy_count
    _cache.clear()
    _negative.clear()
    _source_failures.clear()
    with _busy_lock:
        _busy_count = 0


def count_scrape_attempt(name: str) -> None:
    """Исход попытки скрейпа → counters.scrape_* в /api/metrics.

    Что именно krisha отдаёт IP прода, раньше было видно только в логах
    контейнера, а те живут до ближайшего рестарта — то есть меньше суток
    (.kiro/specs/safeline-468, §7, FR-10).
    """
    metrics.bump(f"scrape_{name}")


def warm_session(url: str) -> bool:
    """Прогрев липкой сессии: один проход пользовательского клиента по объявлению.

    После рестарта хранилище кук пустое, и первая сессия тянет жребий SafeLine —
    с IP Hugging Face часто проигрышный (смоук 2026-09-23: 403, 403, 200). Жребий
    забирает фоновый запрос при старте, а не первый человек
    (.kiro/specs/krisha-session-warmup). Никогда не бросает: True — сессия
    дошла до страницы и её куки запомнены.
    """
    try:
        with user_client(timeout=user_timeout(), on_attempt=count_scrape_attempt) as client:
            ok = client.get(url) is not None
    except Exception:  # noqa: BLE001 — прогрев не должен ронять старт
        logger.warning("krisha: прогрев сессии на %s не удался", url, exc_info=True)
        ok = False
    metrics.bump("session_warmup_ok" if ok else "session_warmup_failed")
    return ok


def _run(url: str, live_vision: bool, wait_s: float) -> dict[str, Any]:
    global _busy_count
    if not _slots.acquire(timeout=max(0.0, wait_s)):
        raise PredictBusy("Сервис перегружен, попробуйте через полминуты")
    with _busy_lock:
        _busy_count += 1
    try:
        return predict_from_url(
            url, live_vision=live_vision, timeout=user_timeout(), on_attempt=count_scrape_attempt
        )
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
    ``InvalidListingUrl`` не кэшируется: это ошибка ввода, скрейпа не было,
    и проверяется она ДО кэша и слотов — мусор не занимает ни лок, ни слот.
    ``SourceUnavailable`` (и его ``ChallengeBlocked``) кэшируется на секунды
    (``SOURCE_FAILURE_TTL_S``): это состояние чужого сервера, а не объявления.
    """
    if not KRISHA_URL_RE.search(url or ""):
        raise InvalidListingUrl("Ожидается ссылка вида https://krisha.kz/a/show/<id>")
    key = cache_key(url, live_vision)
    fresh = _cache.peek(key)
    if fresh is not None and fresh[0] < PREDICT_CACHE_TTL_S:
        return fresh[1]
    failure = _fresh_failure(key)
    if failure is not None:
        raise failure
    since = time.perf_counter()

    def recheck() -> None:
        # Под локом ключа: пока ждали, сосед мог упереться в отказ — он же и наш.
        failed = _fresh_failure(key, since)
        if failed is not None:
            raise failed

    wait = PREDICT_SLOT_WAIT_S if wait_s is None else wait_s
    try:
        return _cache.get_or_call(key, lambda: _run(url, live_vision, wait), recheck=recheck)
    except (PredictBusy, InvalidListingUrl):
        # Занятость — состояние сервиса, а не свойство объявления; кривой URL —
        # ошибка ввода. Ни то, ни другое в негативный кэш не кладём.
        raise
    except SourceUnavailable as exc:
        # Отказ источника (челлендж SafeLine, блок, таймауты) — про сиюминутное
        # состояние ЧУЖОГО сервера: на том же объявлении следующий запрос
        # обычно проходит. Минутный кэш запер бы тысячу человек с одной ссылкой
        # из поста, хотя источник уже пускает, — поэтому только секунды. Их
        # хватает, чтобы один отказ ответил всем, кто ждал тот же скрейп, а
        # брошенные по таймауту потоки не ходили на krisha по очереди.
        _remember(_source_failures, key, exc, lambda stored: (time.perf_counter(), stored))
        raise
    except Exception as exc:
        _remember(_negative, key, exc)
        raise
