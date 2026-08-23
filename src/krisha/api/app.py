"""FastAPI-приложение: /api/predict, /api/health + статичный фронт.

Запуск: `uvicorn krisha.api.app:app --reload`
"""

import fcntl
import functools
import hmac
import ipaddress
import json
import logging
import os
import pathlib
import random
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone

import anyio
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from krisha import __version__, bot, db_release, predict_gate, usage
from krisha.api import metrics, static_cache
from krisha.api.cache import TTLCache
from krisha.api.schemas import (
    DemoResponse,
    HealthResponse,
    PredictRequest,
    PredictResponse,
)
from krisha.config import (
    DB_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    ROOT_DIR,
    feature_forecast,
)
from krisha.db import get_conn, remember_update_id
from krisha.predict import InvalidListingUrl
from krisha.predict_gate import PredictBusy
from krisha.stats import get_stats, heatmap_points

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Startup (замена deprecated @app.on_event) + shutdown-точка расширения."""
    _startup()
    yield


app = FastAPI(title="FairPrice", version=__version__, lifespan=_lifespan)

STATIC_DIR = ROOT_DIR / "static"

# Content-Security-Policy — defense-in-depth поверх экранирования на фронте.
# Ограничивает, куда страница может ходить (img/connect/font) и откуда грузить
# скрипты. 'unsafe-inline' пока нужен для инлайновых <script>/style на страницах;
# при желании их можно вынести в отдельные файлы и убрать 'unsafe-inline'.
CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https://*.kcdn.online https://*.basemaps.cartocdn.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://telegram.org; "
    "connect-src 'self'; "
    "base-uri 'self'; "
    # iframe на странице Space + Telegram Mini App (web-клиенты и webview
    # открывают сайт во фрейме — без этих origins браузер блокирует загрузку)
    "frame-ancestors 'self' https://huggingface.co "
    "https://telegram.org https://*.telegram.org; "
    "object-src 'none'"
)


# Лимит тела запроса: наш самый большой вход — короткий JSON с URL,
# всё существенно большее — мусор или попытка занять память парсером.
#
# Быстрый путь — по заголовку Content-Length (ниже, в _security_headers).
# Он не ловит Transfer-Encoding: chunked без Content-Length — такое тело
# проходит мимо проверки заголовка и парсится целиком (memory-DoS вектор,
# issue #113). Ниже это отдельно закрыто потоковым подсчётом байт в
# _ChunkedBodyLimitMiddleware, которая считает тело по мере поступления
# независимо от заголовков.
MAX_BODY_BYTES = 64 * 1024
DATA_STALE_AFTER_HOURS = 30.0


def _apply_security_headers(response):
    """Навешивает security-заголовки на любой ответ — и обычный, и ранний
    413/400 из проверки размера тела (см. _security_headers ниже)."""
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


class _MaxBodySizeExceeded(Exception):
    pass


class _ChunkedBodyLimitMiddleware:
    """Рав-ASGI мидлварь (не BaseHTTPMiddleware — нужен доступ к сырому
    ``receive``): считает байты тела запроса по мере их поступления, а не
    по заголовку. Ловит Transfer-Encoding: chunked без Content-Length,
    который обходит проверку заголовка в _security_headers (issue #113).

    Работает независимо от порядка регистрации относительно
    _security_headers: где бы эта мидлварь ни оказалась в стеке, она сама
    ловит исключение из собственного вызова ``self.app(...)`` и отвечает
    413 до того, как оно всплывёт наружу — при условии, что до превышения
    лимита обработчик ещё не начал слать ответ (верно для всех текущих
    JSON-эндпоинтов: они сперва целиком читают/валидируют тело).
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        total = 0
        exceeded = False

        async def guarded_send(message):
            # После того как мы сами отправили 413, ответ приложения глушим:
            # два http.response.start в одном запросе — ошибка протокола ASGI.
            if exceeded:
                return
            await send(message)

        async def counted_receive():
            nonlocal total, exceeded
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body") or b"")
                if total > self.max_bytes and not exceeded:
                    # Раньше здесь бросалось исключение, а ловилось оно вокруг
                    # self.app(...). Но исключение поднимается ВНУТРИ стека
                    # FastAPI, который перехватывает ошибки чтения тела и сам
                    # отвечает 400 — до нашего except оно уже не долетало, и
                    # клиент вместо 413 получал 400. Отвечаем прямо здесь.
                    exceeded = True
                    response = _apply_security_headers(
                        JSONResponse(
                            status_code=413, content={"detail": "Слишком большой запрос"}
                        )
                    )
                    await response(scope, receive, send)
                    # Приложению говорим «клиент отключился»: оно свернётся,
                    # а его ответ отбросит guarded_send.
                    return {"type": "http.disconnect"}
            return message

        try:
            await self.app(scope, counted_receive, guarded_send)
        except _MaxBodySizeExceeded:  # pragma: no cover — на всякий случай
            if not exceeded:
                response = _apply_security_headers(
                    JSONResponse(status_code=413, content={"detail": "Слишком большой запрос"})
                )
                await response(scope, receive, send)


app.add_middleware(_ChunkedBodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)

# Сжатие ответов. До этого Space отдавал всё как есть: главная 47 КБ вместо ~10 КБ,
# design.css 34 КБ вместо ~7 КБ. Порог в 600 байт — мелочь жать дороже, чем отдать.
app.add_middleware(GZipMiddleware, minimum_size=600)


def _route_label(request: Request) -> str:
    """Шаблон маршрута, а не сырой путь: иначе /static/... разнесёт метрики
    на сотню ключей, а мусорные URL от сканеров — на тысячу."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return f"{request.method} {path}"
    if request.url.path.startswith("/static/"):
        return f"{request.method} /static/*"
    return f"{request.method} other"


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    started = time.perf_counter()
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > MAX_BODY_BYTES:
                return _apply_security_headers(
                    JSONResponse(status_code=413, content={"detail": "Слишком большой запрос"})
                )
        except ValueError:
            return _apply_security_headers(
                JSONResponse(status_code=400, content={"detail": "Некорректный Content-Length"})
            )
    response = await call_next(request)
    # Одно измерение на запрос: словарь + кольцевой буфер (см. api/metrics).
    # Отдельной мидлварью это стоило бы ещё одного слоя ASGI на каждый запрос.
    metrics.observe(_route_label(request), response.status_code, (time.perf_counter() - started) * 1000)
    return _apply_security_headers(response)


# Кэши горячего пути. /api/health зовут все четыре страницы при каждой
# загрузке, HEALTHCHECK контейнера раз в минуту и keepalive — а внутри он
# читает базу и разбирает JSON метрик. Под наплывом это была самая дорогая
# ручка сайта (issue: бэк под поток людей). TTL секунд не портит смысл ответа:
# возраст данных показывается в часах, метрики меняются раз в переобучение.
HEALTH_CACHE_TTL_S = float(os.environ.get("HEALTH_CACHE_TTL_S", "60"))
# stale_ttl: если база в этот момент занята скрейпером — отдаём прошлый ответ,
# а не 500 всем сразу.
_freshness_cache = TTLCache(ttl=HEALTH_CACHE_TTL_S, stale_ttl=900, maxsize=8)
_model_meta_cache = TTLCache(ttl=300, stale_ttl=3600, maxsize=8)


@app.get("/api/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    # Пусть браузер минуту не переспрашивает: страницы дёргают health при
    # каждой загрузке и в каждой вкладке, а ответ меняется раз в час.
    response.headers["Cache-Control"] = "public, max-age=60"
    # webhook_status() заодно самолечит webhook (не чаще раза в час):
    # keepalive-пинг каждые 6 часов держит бота живым без ручных действий
    data_age_hours, freshness = _data_freshness_cached()
    return HealthResponse(
        status="ok",
        model_loaded=MODEL_PATH.exists(),
        model_error_pct=_model_error_pct(),
        model_median_error_pct=_model_metric_pct("mdape"),
        model_r2=_model_r2(),
        model_mae=_model_mae(),
        model_temporal_validity=_model_temporal_validity(),
        data_age_hours=data_age_hours,
        freshness=freshness,
        tg_webhook=bot.webhook_status(),
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _data_freshness_cached() -> tuple[float | None, str]:
    """То же, что _data_freshness, но не чаще раза в HEALTH_CACHE_TTL_S.

    Ключ — путь к базе: тесты подменяют DB_PATH, и каждая база считается
    отдельно, а не наследует чужой закэшированный ответ.
    """
    return _freshness_cache.get_or_call(str(DB_PATH), _data_freshness)


def _data_freshness() -> tuple[float | None, str]:
    """Возраст данных по максимальному реальному last_seen в базе."""
    if not DB_PATH.exists():
        return None, "stale"
    try:
        with get_conn(DB_PATH) as conn:
            row = conn.execute(
                "SELECT MAX(last_seen) FROM listings WHERE last_seen IS NOT NULL"
            ).fetchone()
    except sqlite3.Error:
        logger.warning("health: не удалось прочитать freshness из базы", exc_info=True)
        return None, "stale"

    observed_at = row[0] if row else None
    if not observed_at:
        return None, "stale"

    observed_dt = _parse_db_datetime(str(observed_at))
    if observed_dt is None:
        return None, "stale"

    age_hours = max(0.0, (_utcnow() - observed_dt).total_seconds() / 3600)
    freshness = "ok" if age_hours <= DATA_STALE_AFTER_HOURS else "stale"
    return round(age_hours, 2), freshness


def _parse_db_datetime(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("health: некорректный last_seen в базе: %r", value)
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _model_metric(name: str) -> float | None:
    """Сырая метрика модели из models/model_meta.json.

    Сайт показывает точность живыми числами, а не переписанными руками: любое
    переобучение меняет их само. `name` — ключ внутри metrics.model
    (mape, mdape, r2).
    """
    meta = _model_meta()
    try:
        value = meta.get("metrics", {}).get("model", {}).get(name)
        if value is None:
            return None
        return float(value)
    except (AttributeError, ValueError, TypeError):
        logger.warning("health: не удалось прочитать метрику %s", name, exc_info=True)
        return None


def _model_meta() -> dict:
    """Содержимое models/model_meta.json с кэшем: файл меняется раз в
    переобучение, а health его читал и парсил на каждый запрос (трижды —
    по разу на метрику)."""

    def read() -> dict:
        if not MODEL_META_PATH.exists():
            return {}
        try:
            data = json.loads(MODEL_META_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("health: не удалось прочитать model_meta.json", exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    # Ключ с mtime: переобучение подменяет файл — свежие метрики видны сразу
    # после записи, не дожидаясь истечения TTL.
    try:
        mtime = MODEL_META_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    return _model_meta_cache.get_or_call((str(MODEL_META_PATH), mtime), read)


def _model_metric_pct(name: str) -> float | None:
    """Доля из meta в процентах — для метрик ошибки (mape, mdape)."""
    value = _model_metric(name)
    return None if value is None else round(value * 100, 1)


def _model_error_pct() -> float | None:
    """Средняя процентная ошибка модели (MAPE)."""
    return _model_metric_pct("mape")


def _model_r2() -> float | None:
    """R² на отложенной выборке. Не процент — показываем как 0.937."""
    value = _model_metric("r2")
    return None if value is None else round(value, 3)


def _model_mae() -> float | None:
    """MAE в тенге. Раньше страница «О проекте» держала это число в разметке
    руками — и оно разъехалось с метой на 0.35 млн ₸."""
    value = _model_metric("mae")
    return None if value is None else round(value)


def _model_temporal_validity() -> bool | None:
    """Подтверждена ли временная валидность оценки (issue #158).

    Отдаём наружу, потому что цифра точности без этой оговорки читается как
    «средняя ошибка модели по рынку Алматы», а это неправда, пока состав
    данных меняется вместе с временем.
    """
    meta = _model_meta()
    value = (meta.get("metrics") or {}).get("temporal_validity")
    return None if value is None else bool(value)


# Анти-спам: скользящее окно запросов на IP (живём в одном процессе — хватает).
# Значения читаются из окружения: за общим NAT мобильного оператора под одним
# адресом сидит целый город, и при наплыве людей 15/мин режет живых
# пользователей. Поднять лимит на проде = переменная + рестарт, без пересборки.
def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


RATE_LIMIT = _env_int("RATE_LIMIT_PER_WINDOW", 15)  # запросов
RATE_WINDOW_S = float(_env_int("RATE_LIMIT_WINDOW_S", 60))
# /api/demo дёргает КАЖДАЯ загрузка главной, а мобильный интернет Казахстана —
# сплошной CGNAT: за одним адресом сидят сотни человек. Строгий лимит на 15
# запросов сломал бы кнопку «Показать на примере» у заметной доли посетителей
# в первую же минуту наплыва — при том, что демо после пула в памяти стоит
# копейки. Строгий лимит нужен только там, где мы ходим на чужой сервер
# (/api/predict). Счётчики у бакетов раздельные: демо не съедает бюджет
# предикта и наоборот.
DEMO_RATE_LIMIT = _env_int("DEMO_RATE_LIMIT_PER_WINDOW", 120)
_rate: dict[str, deque] = defaultdict(deque)
# /api/predict — async def, но _check_rate_limit сам по себе синхронный и
# вызывается из разных потоков threadpool'а (остальные sync-хендлеры вроде
# /api/flags), поэтому мутации _rate (del при вычистке, вставка нового ключа
# через defaultdict) из двух потоков одновременно могут пересечься —
# issue #113: "dictionary changed size during iteration" под нагрузкой.
_rate_lock = threading.Lock()


def _trusted_proxy_hops() -> int:
    """Сколько доверенных reverse-proxy стоят перед приложением.

    Читается функцией, а не константой на импорте: значение меняется
    перезапуском без пересборки образа, а тесты подменяют его monkeypatch'ем.
    Дефолт 1: и HF Spaces, и Railway ставят перед uvicorn ровно один роутер
    (на HF он виден по заголовкам ответа x-proxied-host/replica). 0 —
    приложение доступно напрямую, X-Forwarded-For не доверяем вовсе.
    """
    try:
        return max(0, int(os.environ.get("TRUSTED_PROXY_HOPS", "1")))
    except ValueError:
        return 1


def _client_ip(request: Request) -> str:
    """IP для rate-limit: адрес, вписанный ДОВЕРЕННЫМ ближайшим прокси.

    Берём hops-й элемент X-Forwarded-For СПРАВА. Крайние левые элементы XFF
    полностью подконтрольны клиенту — раньше брался нулевой (левый), и лимит
    обходился сменой заголовка на каждый запрос (проверено на проде HF:
    уникальный XFF снимал лимит, фиксированный — резал как надо). uvicorn как
    источник не годится по той же причине: его ProxyHeadersMiddleware тоже
    берёт крайний левый и кладёт в request.client, поэтому IP читаем из
    заголовка сами. Правый элемент дописывает доверенный прокси HF/Railway —
    на него клиент влиять не может.
    """
    hops = _trusted_proxy_hops()
    if hops > 0:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            parts = [p.strip() for p in fwd.split(",") if p.strip()]
            if parts:
                candidate = parts[-min(hops, len(parts))]
                try:
                    return str(ipaddress.ip_address(candidate))
                except ValueError:
                    pass
    return (request.client.host if request.client else None) or "?"


# issue #110: /api/predict раньше был sync def → крутился в общем anyio
# threadpool (default ~40 потоков) вместе со всеми остальными sync-хендлерами.
# Внутри одного запроса — PoliteClient (сон + сеть/ретраи), CatBoost-инференс,
# SQLite; 40-50 конкурентных predict исчерпывали весь пул, включая
# /api/health (keepalive считал бы Space мёртвым). Явный CapacityLimiter
# ограничивает конкурентность именно тяжёлого пути (predict/flags), не трогая
# лимит остальных (быстрых) sync-хендлеров.
_PREDICT_LIMITER = anyio.CapacityLimiter(10)

MAX_RATE_KEYS = 10_000  # потолок против разрастания памяти (в т.ч. от подделки XFF)


def _check_rate_limit(request: Request, *, bucket: str = "api", limit: int | None = None) -> None:
    ip = f"{bucket}:{_client_ip(request)}"
    limit = RATE_LIMIT if limit is None else limit
    now = time.monotonic()
    with _rate_lock:
        # Вытесняем протухшие ключи, чтобы словарь не рос бесконечно.
        if len(_rate) > MAX_RATE_KEYS:
            for key in [k for k, dq in _rate.items() if not dq or now - dq[-1] > RATE_WINDOW_S]:
                del _rate[key]
        q = _rate[ip]
        while q and now - q[0] > RATE_WINDOW_S:
            q.popleft()
        if len(q) >= limit:
            # Retry-After: клиенту (и Telegram-боту, и браузеру) видно, через
            # сколько секунд окно освободится, — вместо гадания и долбёжки.
            retry_after = max(1, int(RATE_WINDOW_S - (now - q[0])) + 1)
            metrics.bump("rate_limited")
            raise HTTPException(
                status_code=429,
                detail="Слишком много запросов, подожди минуту",
                headers={"Retry-After": str(retry_after)},
            )
        q.append(now)


# Кэш, single-flight, негативный кэш и слоты живут в krisha.predict_gate —
# общей калитке для веба и бота (раньше бот ходил в predict_from_url напрямую,
# мимо всех предохранителей). Здесь остаётся только HTTP-обвязка.
#
# Сколько всего ждать ответа предикта, прежде чем честно сказать «занято».
# Чуть больше, чем ожидание слота в калитке (PREDICT_SLOT_WAIT_S): при
# перегрузе первым должен срабатывать её осмысленный PredictBusy, а этот
# таймаут — страховка от «поток занят чем-то ещё».
PREDICT_WAIT_S = float(os.environ.get("PREDICT_WAIT_S", "20"))
_BUSY_RETRY_AFTER = "30"


@app.post("/api/predict", response_model=PredictResponse)
async def predict(req: PredictRequest, request: Request) -> PredictResponse:
    # Кэш проверяем ДО рейт-лимита. Главный сценарий наплыва — тысяча человек
    # с ОДНОЙ ссылкой из поста, и сидят они за десятком CGNAT-адресов
    # мобильных операторов. Резать их 429 на готовый ответ, который уже лежит
    # в памяти и не стоит ничего, — терять живых пользователей ни за что.
    # Лимит защищает поход на krisha.kz, а не отдачу байтов из словаря.
    cached = predict_gate.peek(req.url)
    if cached is not None:
        metrics.bump("predict_cache_hit")
        # Событие статистики считаем и для попадания в кэш: это живой человек.
        await anyio.to_thread.run_sync(functools.partial(usage.record_event, "predict"))
        return PredictResponse(**cached)
    _check_rate_limit(request)
    try:
        # live_vision=False: веб отвечает сразу и не ходит в Gemini Vision
        # (он и так за фича-флагом, issue #157) — живой запрос только у бота.
        # issue #110: явный CapacityLimiter вместо дефолтного sync-threadpool —
        # тяжёлый путь (скрейп + CatBoost + SQLite) не делит пул с health/site.
        # move_on_after: ограничиваем ОЖИДАНИЕ, а не работу. Если за
        # PREDICT_WAIT_S очередь не рассосалась — быстрый 503 с Retry-After
        # лучше, чем двести висящих спиннеров и скрейпы для тех, кто уже ушёл.
        # abandon_on_cancel=True: уже начатый в потоке разбор доработает сам и
        # ляжет в кэш — следующему повезёт.
        with anyio.move_on_after(PREDICT_WAIT_S) as cancel_scope:
            result = await anyio.to_thread.run_sync(
                functools.partial(predict_gate.cached_predict, req.url, live_vision=False),
                abandon_on_cancel=True,
                limiter=_PREDICT_LIMITER,
            )
        if cancel_scope.cancel_called:
            metrics.bump("predict_wait_timeout")
            raise HTTPException(
                status_code=503,
                detail="Сервис перегружен, попробуй через полминуты",
                headers={"Retry-After": _BUSY_RETRY_AFTER},
            )
    except PredictBusy:
        metrics.bump("predict_busy")
        raise HTTPException(
            status_code=503,
            detail="Сервис перегружен, попробуй через полминуты",
            headers={"Retry-After": _BUSY_RETRY_AFTER},
        ) from None
    except InvalidListingUrl as exc:
        # 422 — пользовательская валидация URL, текст безопасен и полезен
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError:
        # Любой ДРУГОЙ ValueError — внутренний сбой, а не ошибка ввода.
        # Например json.JSONDecodeError (подкласс ValueError) на битом
        # model_meta.json: раньше он уезжал пользователю как 422 с сырым
        # текстом исключения, включая кусок содержимого файла.
        logger.exception("predict: внутренняя ошибка обработки")
        raise HTTPException(status_code=502, detail="Не удалось обработать объявление") from None
    except FileNotFoundError:
        # детали (пути и т.п.) — в лог, наружу обобщённо
        logger.exception("predict: модель/файл недоступны")
        raise HTTPException(status_code=503, detail="Сервис временно недоступен") from None
    except RuntimeError:
        logger.exception("predict: ошибка обработки объявления")
        raise HTTPException(status_code=502, detail="Не удалось обработать объявление") from None
    # record_event раз в FLUSH_INTERVAL уходит в _flush → запись файла и
    # PUT в api.github.com (два httpx-вызова с таймаутом 15 с). Здесь мы в
    # `async def`, то есть в потоке event loop'а: синхронный вызов вешал бы
    # ВЕСЬ сервер на время похода в GitHub — включая /api/health, по
    # которому keepalive решает, жив ли Space.
    await anyio.to_thread.run_sync(functools.partial(usage.record_event, "predict"))
    return PredictResponse(**result)


# issue #157: эндпоинт /api/flags/{listing_id} удалён вместе со всем путём
# LLM-бейджей. Абляция на честном сплите показала, что как фичи они ухудшают
# модель (R² 0.79 → 0.76), в неё они не идут, и оставались украшением карточки
# ценой похода в Gemini на каждый предикт и кэша, который всё равно стирался
# при каждом рестарте Space. Фронт больше не догружает флаги.


# Пул кандидатов для «Показать на примере». Было `ORDER BY RANDOM() LIMIT 1`:
# SQLite для этого читает ВСЕ активные объявления, считает каждому случайный
# ключ и сортирует — 130+ мс на запрос и полный проход по 168-мегабайтной
# базе на каждое нажатие кнопки. Берём сотню свежих лотов раз в DEMO TTL и
# кидаем кубик уже в памяти: случайность для пользователя та же.
DEMO_POOL_TTL_S = float(os.environ.get("DEMO_POOL_TTL_S", "600"))
DEMO_POOL_SIZE = 100
_demo_pool_cache = TTLCache(ttl=DEMO_POOL_TTL_S, stale_ttl=3600, maxsize=4)


def _demo_pool() -> list[tuple[int, str]]:
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT id, url
            FROM listings
            WHERE is_active = 1
              AND url IS NOT NULL
              AND url LIKE '%krisha.kz/a/show/%'
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (DEMO_POOL_SIZE,),
        ).fetchall()
    return [(int(r["id"]), str(r["url"])) for r in rows]


@app.get("/api/demo", response_model=DemoResponse)
def demo(request: Request) -> DemoResponse:
    """URL живого активного объявления для кнопки «Показать на примере»."""
    _check_rate_limit(request, bucket="demo", limit=DEMO_RATE_LIMIT)
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Демо-объявление временно недоступно")
    pool = _demo_pool_cache.get_or_call(str(DB_PATH), _demo_pool)
    if not pool:
        raise HTTPException(status_code=503, detail="Демо-объявление временно недоступно")
    listing_id, url = random.choice(pool)
    return DemoResponse(listing_id=listing_id, url=url)


STATS_CACHE_TTL = 600  # секунд
# Раньше это был dict со временем: пока значение свежее — хорошо, но в момент
# истечения TTL под нагрузкой в get_stats() проваливались ВСЕ запросы разом
# (сто параллельных читателей — сто полных пересчётов по базе, каждый под GIL).
# TTLCache пускает внутрь одного, остальные ждут его результат.
_stats_cache = TTLCache(ttl=STATS_CACHE_TTL, stale_ttl=3600, maxsize=2)


@app.get("/api/stats")
def stats(response: Response) -> dict:
    """Статистика рынка: всего объявлений, ₸/м² по районам, распределение цен."""
    # max-age меньше серверного TTL: на сервере значение живёт 10 минут,
    # у клиента 5 — никто не увидит цифры старше, чем они есть на бэке.
    response.headers["Cache-Control"] = "public, max-age=300"
    try:
        return _stats_cache.get_or_call("stats", get_stats)
    except FileNotFoundError:
        logger.exception("stats: данные недоступны")
        raise HTTPException(status_code=503, detail="Статистика временно недоступна") from None


_heatmap_cache = TTLCache(ttl=STATS_CACHE_TTL, stale_ttl=3600, maxsize=2)


@app.get("/api/heatmap")
def heatmap(response: Response) -> list[dict]:
    """Сетка ₸/м² для карты: ячейки ~400 м по активным лотам с координатами."""
    response.headers["Cache-Control"] = "public, max-age=300"
    try:
        return _heatmap_cache.get_or_call("heatmap", heatmap_points)
    except FileNotFoundError:
        logger.exception("heatmap: база недоступна")
        raise HTTPException(status_code=503, detail="Карта временно недоступна") from None


_forecast_cache = TTLCache(ttl=STATS_CACHE_TTL, stale_ttl=3600, maxsize=2)


@app.get("/api/forecast")
def forecast() -> dict:
    """Прогноз ₸/м² на 3–6 месяцев: линейный тренд недельных медиан по районам.

    issue #157: за фича-флагом FEATURE_FORECAST, по умолчанию выключен.
    Экстраполировать полгода по истории короче двух месяцев, которая вдобавок
    дважды прерывалась провалами сбора, — значит показывать пользователю
    уверенное число, за которым ничего не стоит. Полугодовой горизонт и так
    не отображался никогда: данных на него нет.
    """
    if not feature_forecast():
        raise HTTPException(status_code=404, detail="Прогноз отключён")
    from krisha.forecast import build_forecast

    try:
        return _forecast_cache.get_or_call("forecast", build_forecast)
    except FileNotFoundError:
        logger.exception("forecast: база недоступна")
        raise HTTPException(status_code=503, detail="Прогноз временно недоступен") from None


@app.get("/api/metrics", include_in_schema=False)
def api_metrics() -> dict:
    """Что происходило с этим воркером: счётчики, задержки, очередь предикта.

    Публично и безопасно: только агрегаты, ни одного пользовательского данного.
    Смысл — узнать правду о пике, не долбя прод нагрузкой (HF банит по IP).
    При WEB_CONCURRENCY=2 ответ приходит от того воркера, которому достался
    запрос: цифры «примерно», зато без внешнего мониторинга.
    """
    data = metrics.snapshot()
    stats_ = _PREDICT_LIMITER.statistics()
    data["predict"] = {
        **predict_gate.stats(),
        "limiter_borrowed": stats_.borrowed_tokens,
        "limiter_total": int(stats_.total_tokens),
        "limiter_waiting": stats_.tasks_waiting,
    }
    data["assets"] = {"precompressed": len(_ASSETS)}
    return data


# Дедуп апдейтов Telegram: медленный предикт внутри хендлера раньше приводил
# к таймауту вебхука → Telegram ретраил тот же update_id → дубли ответов.
# Помним последние N обработанных id (процесс один, память — достаточно).
_SEEN_UPDATE_IDS: deque[int] = deque(maxlen=1000)


def _process_tg_update(update: dict) -> None:
    try:
        bot.handle_update(update)
    except Exception:  # бот не должен ронять вебхук
        logger.exception("Ошибка обработки Telegram-апдейта")


@app.post("/tg/webhook", include_in_schema=False)
def telegram_webhook(
    update: dict,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    """Webhook Telegram-бота. Telegram шлёт сюда апдейты после setWebhook.

    Отвечаем 200 сразу, обработку (парсинг страницы + предикт — секунды)
    уносим в background task: Telegram не успевает затаймаутить вебхук
    и не присылает ретраи. Повторные update_id молча подтверждаем.
    """
    token = bot.bot_token()
    if not token:
        raise HTTPException(status_code=404)
    # Сравниваем в байтах: hmac.compare_digest на str требует, чтобы ОБА
    # аргумента были ASCII-only, иначе бросает TypeError — а заголовок
    # приходит от кого угодно. Starlette декодирует заголовки как latin-1,
    # поэтому обратно кодируем тоже latin-1 (utf-8 исказил бы байты).
    # Без этого подделанный заголовок с не-ASCII давал 500 вместо 403.
    secret = (x_telegram_bot_api_secret_token or "").encode("latin-1", "ignore")
    if not secret or not hmac.compare_digest(secret, bot.webhook_secret(token).encode("ascii")):
        raise HTTPException(status_code=403)
    update_id = update.get("update_id")
    if isinstance(update_id, int):
        if update_id in _SEEN_UPDATE_IDS:
            return {"ok": True}
        _SEEN_UPDATE_IDS.append(update_id)
        # Второй рубеж — общий для всех воркеров (см. WEB_CONCURRENCY): свой
        # deque у каждого процесса, и ретрай, попавший в соседа, иначе
        # обработался бы второй раз.
        if DB_PATH.exists() and not remember_update_id(update_id):
            return {"ok": True}
    background_tasks.add_task(_process_tg_update, update)
    return {"ok": True}


@contextmanager
def _startup_lock():
    """Один воркер готовит окружение, остальные ждут.

    При WEB_CONCURRENCY > 1 uvicorn поднимает несколько процессов, и каждый
    выполняет _startup. Без блокировки два процесса одновременно качали бы
    из релиза одну и ту же базу на 168 МБ и гнали бы миграции по одному
    файлу (SQLite при этом ловит "database is locked"). Под флоком первый
    делает работу, второй входит уже на готовое: база на месте — скачивание
    пропускается, миграции идемпотентны.
    """
    lock_path = DB_PATH.parent / ".startup.lock"
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "w")
        fcntl.flock(handle, fcntl.LOCK_EX)
    except OSError:  # read-only ФС и т.п. — работаем как раньше
        logger.warning("не удалось взять стартовую блокировку", exc_info=True)
        handle = None
    try:
        yield
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()


def _startup() -> None:
    _log_runtime_limits()
    with _startup_lock():
        _prepare_data()
    _warmup_runtime_caches()
    bot.setup_webhook()


def _log_runtime_limits() -> None:
    """Сколько CPU нам реально дали. Одна строка в логе, снимающая главную
    неопределённость всех замеров: os.cpu_count() показывает ядра ХОСТА, а
    контейнеру может быть выдана квота вдвое меньше — и тогда авто-сайзинг по
    числу ядер врёт, а WEB_CONCURRENCY подобран вслепую."""
    try:
        affinity: int | None = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):  # pragma: no cover — не Linux
        affinity = None
    quota = "?"
    for path in ("/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"):
        try:
            quota = pathlib.Path(path).read_text().strip()
            break
        except OSError:
            continue
    logger.info(
        "runtime: cpu_count=%s affinity=%s cgroup=%s workers=%s omp=%s",
        os.cpu_count(),
        affinity,
        quota,
        os.environ.get("WEB_CONCURRENCY", "?"),
        os.environ.get("OMP_NUM_THREADS", "?"),
    )


def _prepare_data() -> None:
    # База не хранится в git — при старте скачиваем её из GitHub Release.
    db_release.ensure_db()
    # Модели пока коммитятся в main (переходный период issue #74) — скачиваем
    # из model-latest только если локального models/model.cbm нет вообще
    # (например, .gitignore уже включил models/*.cbm на будущем шаге).
    db_release.ensure_models()
    if DB_PATH.exists():
        # Скачанная база могла не проходить init_db: догоняем миграции
        # и индексы (idx_listings_fingerprint для проверки дублей).
        from krisha.db import init_db

        init_db()


def _warmup_runtime_caches() -> None:
    """Прогревает тяжёлые runtime-кэши после cold start HF Space.

    Первый пользовательский /api/predict иначе платит за загрузку CatBoost-моделей,
    spatial/OSM JSON-снапшотов и построение KD-деревьев. Всё fail-soft: если
    модель/снапшот недоступны, приложение всё равно стартует и отдаст понятную
    ошибку уже на конкретном endpoint.
    """
    try:
        from krisha.geo import load_poi_index
        from krisha.predict import load_interval_models, load_model
        from krisha.spatial import load_spatial_ref

        load_model()
        load_interval_models()
        load_spatial_ref()
        load_poi_index()
        # Статистика использования: иначе её json с диска читает первый же
        # посетитель — под глобальным локом и ровно в момент наплыва.
        usage.warm()
        # Агрегаты страниц: первый посетитель после рестарта иначе платит за
        # полный пересчёт по базе (а при наплыве он платит не один).
        if DB_PATH.exists():
            warmups = (
                ("stats", _stats_cache, "stats", get_stats),
                ("heatmap", _heatmap_cache, "heatmap", heatmap_points),
                ("demo", _demo_pool_cache, str(DB_PATH), _demo_pool),
            )
            for name, cache, key, producer in warmups:
                try:
                    cache.get_or_call(key, producer)
                except Exception:  # noqa: BLE001, PERF203 — прогрев не критичен
                    logger.warning("warmup: %s не прогрелся", name, exc_info=True)
        logger.info("runtime caches warmed up")
    except Exception:  # noqa: BLE001 — warmup не должен валить запуск Space
        logger.warning("runtime warmup failed", exc_info=True)


# ---------------------------------------------------------------- статика
# Всё текстовое (html/css/js) читается и жмётся ОДИН раз при старте и живёт в
# памяти процесса: см. krisha.api.static_cache — там же цифры замера, ради
# которых это сделано (главная: 361 rps с gzip против 706 без — половина CPU
# уходила на повторное сжатие одного и того же файла).
HTML_CACHE_CONTROL = "no-cache"  # не «не кэшировать», а «спроси ETag»
# Шрифты и картинки не меняются под тем же именем — их можно держать у клиента
# год. Стили и скрипты меняются вместе с релизом, поэтому им no-cache: браузер
# спросит ETag и почти всегда получит 304 вместо повторной загрузки.
IMMUTABLE_SUFFIXES = (".woff2", ".woff", ".webp", ".png", ".jpg", ".svg", ".ico")
PRECOMPRESS_SUFFIXES = (".html", ".css", ".js", ".mjs", ".json", ".webmanifest")
_ASSETS: dict[str, static_cache.Asset] = {}


def _asset_names() -> list[str]:
    if not STATIC_DIR.exists():
        return []
    names = set()
    for suffix in PRECOMPRESS_SUFFIXES:
        for path in STATIC_DIR.rglob(f"*{suffix}"):
            if path.is_file():
                names.add(path.relative_to(STATIC_DIR).as_posix())
    return sorted(names)


def _build_assets() -> None:
    """Собирается на импорте модуля (то есть в каждом воркере) — файлы в
    образе до рестарта неизменны, перепроверять их на запросе незачем."""
    global _ASSETS
    _ASSETS = static_cache.build_cache(STATIC_DIR, _asset_names())


_build_assets()


def _asset_response(
    request: Request,
    name: str,
    *,
    status_code: int = 200,
    cache_control: str = HTML_CACHE_CONTROL,
) -> Response:
    """Отдаёт файл из памяти: нужный вариант (gzip/сырой), ETag, 304, HEAD."""
    asset = _ASSETS.get(name)
    if asset is None:  # dev: файл появился после старта — обычная отдача с диска
        path = STATIC_DIR / name
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(
            path, status_code=status_code, headers={"Cache-Control": cache_control}
        )
    status, body, headers = static_cache.negotiate(
        asset,
        accept_encoding=request.headers.get("accept-encoding"),
        if_none_match=request.headers.get("if-none-match"),
        cache_control=cache_control,
    )
    if status == 200 and status_code != 200:
        status = status_code
    if request.method == "HEAD":
        body = b""  # заголовки (включая Content-Length) остаются настоящими
    return Response(content=body, status_code=status, headers=headers)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    """Браузеру — оформленная страница, любому клиенту API — обычный JSON."""
    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html and not request.url.path.startswith("/api/"):
        try:
            return _apply_security_headers(_asset_response(request, "404.html", status_code=404))
        except HTTPException:
            pass
    detail = getattr(exc, "detail", "Не найдено")
    return _apply_security_headers(JSONResponse(status_code=404, content={"detail": detail}))


# Страницы — async def: отдавать байты из памяти нечему блокировать, а поход
# в тредпул на каждый показ страницы под наплывом создавал очередь ровно там,
# где её быть не должно. usage.record_event внутри держит глобальный лок на
# пару инкрементов словаря (микросекунды), сеть из-под него давно унесена в
# демон-поток, состояние читается на старте (usage.warm).
# HEAD объявлен рядом с GET намеренно: аптайм-мониторы и HEALTHCHECK ходят
# именно им, а FastAPI (в отличие от голого Starlette) сам его не добавляет —
# без этого страница отвечала 405. Посещение по HEAD не считаем.
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def index(request: Request) -> Response:
    if request.method == "GET":
        usage.record_event("site")
    return _asset_response(request, "index.html")


@app.api_route("/stats", methods=["GET", "HEAD"], include_in_schema=False)
async def stats_page(request: Request) -> Response:
    if request.method == "GET":
        usage.record_event("site")
    return _asset_response(request, "stats.html")


@app.api_route("/about", methods=["GET", "HEAD"], include_in_schema=False)
async def about_page(request: Request) -> Response:
    if request.method == "GET":
        usage.record_event("site")
    return _asset_response(request, "about.html")


class _CachedStatic(StaticFiles):
    """Текст — из предсжатой памяти, бинарь — обычной отдачей файла."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        name = path.lstrip("/")
        if name in _ASSETS and scope.get("method") in ("GET", "HEAD"):
            return _asset_response(Request(scope), name, cache_control="no-cache")
        return await super().get_response(path, scope)

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        path = str(getattr(response, "path", ""))
        if path.endswith(IMMUTABLE_SUFFIXES):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


if STATIC_DIR.exists():
    app.mount("/static", _CachedStatic(directory=STATIC_DIR), name="static")
