"""FastAPI-приложение: /api/predict, /api/health + статичный фронт.

Запуск: `uvicorn krisha.api.app:app --reload`
"""

try:
    import fcntl
except ImportError:  # Windows: README обещает локальную разработку, там один процесс
    fcntl = None
import dataclasses
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
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import parse_qs, urlencode

import anyio
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from krisha import __version__, bot, db_release, predict_gate, usage
from krisha.api import live_pages, metrics, site_analytics, static_cache
from krisha.api.cache import TTLCache
from krisha.api.schemas import (
    DemoResponse,
    HealthResponse,
    PredictRequest,
    PredictResponse,
)
from krisha.config import (
    DATA_DIR,
    DB_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    RENT_DB_PATH,
    RENT_MODEL_META_PATH,
    RENT_MODEL_PATH,
    RENT_SPATIAL_REF_PATH,
    ROOT_DIR,
    feature_forecast,
)
from krisha.db import data_observed_at, get_conn, remember_update_id
from krisha.predict import InvalidListingUrl, ListingNotFound
from krisha.predict_gate import PredictBusy
from krisha.scraping.client import ChallengeBlocked, SourceUnavailable
from krisha.stats import compute_rent_stats, get_stats, heatmap_points

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Startup (замена deprecated @app.on_event) + shutdown-точка расширения."""
    _startup()
    yield


app = FastAPI(title="FairPrice", version=__version__, lifespan=_lifespan)
# Адрес со слэшем на конце (/about/) Starlette сам переводил на адрес без него —
# 307 с абсолютным http://<хост Space>/about: за прокси Space приложение видит
# http, воркер Cloudflare переписывал только https-адреса Space, и посетитель
# bagam.info/about/ уезжал на зеркало. Теперь это 301 с относительным адресом
# в обработчике 404 (not_found ниже) — он не зависит ни от схемы, ни от хоста.
app.router.redirect_slashes = False

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
    # static.cloudflareinsights.com / cloudflareinsights.com — маяк Cloudflare Web
    # Analytics, который Cloudflare сам дописывает в html на домене; без них
    # браузер режет скрипт с ошибкой в консоли, а статистика посещений пустая.
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://telegram.org "
    "https://static.cloudflareinsights.com; "
    "connect-src 'self' https://cloudflareinsights.com; "
    "base-uri 'self'; "
    # iframe на странице Space + Telegram Mini App (web-клиенты и webview
    # открывают сайт во фрейме — без этих origins браузер блокирует загрузку)
    "frame-ancestors 'self' https://huggingface.co "
    "https://telegram.org https://*.telegram.org; "
    "object-src 'none'"
)
# Счётчики посещаемости (site_analytics): их адреса дописываются в CSP, только
# когда номера счётчиков заданы в окружении, — иначе политика ровно CSP выше.
# Оба значения пересобирает _build_assets: тесты меняют окружение и пересобирают.
_ANALYTICS = site_analytics.Config()
_ACTIVE_CSP = CSP
# Возможности браузера, которые сайту не нужны. Буфер обмена (кнопка «Вставить»)
# здесь не упомянут: он остаётся по умолчанию — только после нажатия.
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
# Служебные адреса: поисковику в индексе они не нужны, даже если ссылка на них
# где-то найдётся (robots.txt запрещает обход, но не индекс по внешней ссылке).
_NOINDEX_PREFIXES = ("/api/", "/tg/", "/docs", "/redoc", "/openapi.json", "/livez", "/readyz")


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
    response.headers.setdefault("Content-Security-Policy", _ACTIVE_CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
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

# Бинарная статика, которая уже сжата внутри формата. Тип задаём явно: StaticFiles
# берёт его из mimetypes, а в python:3.11-slim нет /etc/mime.types, и встроенная
# таблица не знает webp/woff2 — прод отдавал их как application/octet-stream
# при nosniff (.kiro/specs/static-binary-headers).
_BINARY_MEDIA_TYPES = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ico": "image/x-icon",
}


class _GZipExceptCompressed(GZipMiddleware):
    """GZip для всего, кроме уже сжатых форматов.

    starlette 1.3.1 (прод-лок) жмёт всё подряд, кроме text/event-stream: webp,
    woff2 и png шли через gzip-9 на каждом запросе — CPU на 2 vCPU без выигрыша
    в размере. Решаем по пути до ответа — не зависим ни от типа, ни от версии.
    """

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].lower().endswith(tuple(_BINARY_MEDIA_TYPES)):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, _dedupe_vary(send))


def _dedupe_vary(send):
    """Ответы из памяти (статика, /api/stats) сами ставят Vary: Accept-Encoding,
    а starlette дописывает его ещё раз к несжатому ответу — выходило
    «Accept-Encoding, Accept-Encoding». Повторы убираем."""

    async def wrapped(message):
        if message["type"] == "http.response.start":
            headers = MutableHeaders(raw=message["headers"])
            vary = headers.get("vary")
            if vary and "," in vary:
                seen: dict[str, str] = {}
                for item in (v.strip() for v in vary.split(",")):
                    if item:
                        seen.setdefault(item.lower(), item)
                headers["vary"] = ", ".join(seen.values())
        await send(message)

    return wrapped


# Сжатие ответов. До этого Space отдавал всё как есть: главная 47 КБ вместо ~10 КБ,
# design.css 34 КБ вместо ~7 КБ. Порог в 600 байт — мелочь жать дороже, чем отдать.
app.add_middleware(_GZipExceptCompressed, minimum_size=600)


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
    # issue #190 §2.7: идентификатор запроса — клиентский X-Request-ID (если
    # похож на идентификатор) или свой. Возвращается заголовком, чтобы жалобу
    # «не сработало» можно было сопоставить со строкой лога, а не гадать по
    # времени. В структуре ответов ничего не меняется.
    request_id = _request_id(request)
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers.setdefault("X-Request-ID", request_id)
    if request.url.path.startswith(_NOINDEX_PREFIXES):
        response.headers.setdefault("X-Robots-Tag", "noindex")
    # Одно измерение на запрос: словарь + кольцевой буфер (см. api/metrics).
    # Отдельной мидлварью это стоило бы ещё одного слоя ASGI на каждый запрос.
    metrics.observe(_route_label(request), response.status_code, (time.perf_counter() - started) * 1000)
    return _apply_security_headers(response)


_REQUEST_ID_MAX = 64


def _request_id(request: Request) -> str:
    """X-Request-ID клиента (буквы/цифры/`-_.`, ≤64) либо новый uuid4 без дефисов."""
    raw = request.headers.get("x-request-id", "").strip()
    if raw and len(raw) <= _REQUEST_ID_MAX and all(
        c.isalnum() or c in "-_." for c in raw
    ):
        return raw
    return uuid.uuid4().hex


# Кэши горячего пути. /api/health зовут все четыре страницы при каждой
# загрузке, HEALTHCHECK контейнера раз в минуту и keepalive — а внутри он
# читает базу и разбирает JSON метрик. Под наплывом это была самая дорогая
# ручка сайта (issue: бэк под поток людей). TTL секунд не портит смысл ответа:
# возраст данных показывается в часах, метрики меняются раз в переобучение.
HEALTH_CACHE_TTL_S = float(os.environ.get("HEALTH_CACHE_TTL_S", "60"))


def _build_revision() -> str | None:
    """Коммит, из которого собран этот образ, или None вне деплоя.

    Пишется в data/build_revision.txt воркфлоу деплоя (см. deploy-hf.yml).
    Без него смоук после выката не мог отличить новый контейнер от ещё
    работающего старого: старый честно отвечает 200 всё время пересборки,
    и прогон «подтверждал» предыдущий деплой. Метаданные Space для этого не
    годятся — при пуше снапшота у HF свой sha, с нашим он не совпадает
    никогда. Читаем один раз при импорте: файл запечён в образ.
    """
    env = (os.environ.get("BUILD_REVISION") or "").strip()
    if env:
        return env[:64]
    try:
        return ((DATA_DIR / "build_revision.txt").read_text(encoding="utf-8").strip() or None)
    except OSError:
        return None


BUILD_REVISION = _build_revision()
# stale_ttl: если база в этот момент занята скрейпером — отдаём прошлый ответ,
# а не 500 всем сразу.
_freshness_cache = TTLCache(ttl=HEALTH_CACHE_TTL_S, stale_ttl=900, maxsize=8)
_model_meta_cache = TTLCache(ttl=300, stale_ttl=3600, maxsize=8)


def _json_asset(payload) -> static_cache.Asset:
    """JSON-ответ API как готовое представление: байты, gzip-вариант, ETag."""
    raw = json.dumps(
        jsonable_encoder(payload), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return static_cache.asset_from_bytes(raw, "application/json")


def _cacheable_json(request: Request, asset: static_cache.Asset, max_age: int) -> Response:
    """Ответ, который браузер честно кэширует: max-age + ETag + Vary.

    После max-age браузер переспрашивает с If-None-Match и получает 304 без
    тела. gzip-вариант со своим ETag и Vary: Accept-Encoding — как у статики
    (static_cache.negotiate): GZipMiddleware навешивал бы ОДИН ETag на оба
    представления, и кэш мог отдать сжатое клиенту без gzip.
    """
    status, body, headers = static_cache.negotiate(
        asset,
        accept_encoding=request.headers.get("accept-encoding"),
        if_none_match=request.headers.get("if-none-match"),
        cache_control=f"public, max-age={max_age}",
    )
    return Response(content=body, status_code=status, headers=headers)


@app.get("/api/health", response_model=HealthResponse)
def health(request: Request) -> Response:
    # Пусть браузер минуту не переспрашивает: страницы дёргают health при
    # каждой загрузке и в каждой вкладке, а ответ меняется раз в час.
    # webhook_status() заодно самолечит webhook (не чаще раза в час):
    # keepalive-пинг каждые 6 часов держит бота живым без ручных действий
    data_age_hours, freshness = _data_freshness_cached()
    payload = HealthResponse(
        status="ok",
        model_loaded=MODEL_PATH.exists(),
        model_error_pct=_model_error_pct(),
        model_median_error_pct=_model_metric_pct("mdape"),
        model_r2=_model_r2(),
        model_mae=_model_mae(),
        model_temporal_validity=_model_temporal_validity(),
        model_error_ci_pct=_model_error_ci_pct(),
        data_age_hours=data_age_hours,
        freshness=freshness,
        tg_webhook=bot.webhook_status(),
        revision=BUILD_REVISION,
        rent_model_loaded=RENT_MODEL_PATH.exists(),
        rent_model_error_pct=_rent_model_error_pct(),
    )
    return _cacheable_json(request, _json_asset(payload.model_dump(mode="json")), max_age=60)


def _rent_model_error_pct() -> float | None:
    """MAPE модели аренды в процентах из её меты (кэш — как у продажной)."""
    def load() -> float | None:
        try:
            meta = json.loads(RENT_MODEL_META_PATH.read_text(encoding="utf-8"))
            return round(float(meta["metrics"]["model"]["mape"]) * 100, 1)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    return _model_meta_cache.get_or_call(f"rent:{RENT_MODEL_META_PATH}", load)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _data_freshness_cached() -> tuple[float | None, str]:
    """То же, что _data_freshness, но не чаще раза в HEALTH_CACHE_TTL_S.

    Ключ — путь к базе: тесты подменяют DB_PATH, и каждая база считается
    отдельно, а не наследует чужой закэшированный ответ.
    """
    return _freshness_cache.get_or_call(str(DB_PATH), _data_freshness)


def _data_freshness() -> tuple[float | None, str]:
    """Возраст данных: когда сборщик последний раз прошёл по рынку.

    Источник — db.data_observed_at (конец последнего прохода по sweep_runs,
    у старых баз — last_seen строк сборщика, а не пользовательских проверок).
    Тот же момент отдают /api/stats (updated_at) и sitemap (lastmod).
    """
    observed_dt = _data_observed_at()
    if observed_dt is None:
        return None, "stale"
    age_hours = max(0.0, (_utcnow() - observed_dt).total_seconds() / 3600)
    freshness = "ok" if age_hours <= DATA_STALE_AFTER_HOURS else "stale"
    return round(age_hours, 2), freshness


def _data_observed_at() -> datetime | None:
    """Момент последнего прохода сборщика продажи (None — базы/данных нет)."""
    if not DB_PATH.exists():
        return None
    try:
        with get_conn(DB_PATH) as conn:
            return data_observed_at(conn)
    except sqlite3.Error:
        logger.warning("health: не удалось прочитать freshness из базы", exc_info=True)
        return None


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


def _model_error_ci_pct() -> list[float] | None:
    """95% ДИ MAPE (metrics.model_mape_ci.{lo,hi}) в процентах, [lo, hi]."""
    ci = _model_meta().get("metrics", {}).get("model_mape_ci") or {}
    try:
        lo, hi = ci.get("lo"), ci.get("hi")
        if lo is None or hi is None:
            return None
        return [round(float(lo) * 100, 1), round(float(hi) * 100, 1)]
    except (AttributeError, ValueError, TypeError):
        return None


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


def _worker_count() -> int:
    """Сколько процессов uvicorn делят один сокет (Dockerfile: WEB_CONCURRENCY)."""
    try:
        return max(1, int(os.environ.get("WEB_CONCURRENCY", "1")))
    except ValueError:
        return 1


def _per_process_limit(total: int, workers: int) -> int:
    """Доля общего лимита на IP, приходящаяся на один процесс.

    Счётчик rate-limit живёт в памяти процесса, а процессов при
    WEB_CONCURRENCY=2 два — значит настроенные «15 запросов в минуту на IP»
    на деле означали до 30: запросы одного клиента раскладываются по обоим
    воркерам, и каждый считает свои. Заявленное в конфиге число обязано
    означать то, что написано, поэтому делим его между процессами.

    Пол в 5 запросов — защита от обратной крайности: при большом числе
    воркеров и малом лимите доля выродилась бы в 1–2 запроса, и живой человек
    ловил бы 429 на второй проверке объявления. Из-за keep-alive соединение
    клиента липнет к одному воркеру, так что в худшем случае ему достаётся
    именно эта доля — ошибаться здесь лучше в строгую сторону, чем в
    молчаливое удвоение.
    """
    return max(5, -(-total // max(1, workers)))


RATE_LIMIT_TOTAL = _env_int("RATE_LIMIT_PER_WINDOW", 15)  # запросов на IP суммарно
RATE_LIMIT = _per_process_limit(RATE_LIMIT_TOTAL, _worker_count())
RATE_WINDOW_S = float(_env_int("RATE_LIMIT_WINDOW_S", 60))
# /api/demo дёргает КАЖДАЯ загрузка главной, а мобильный интернет Казахстана —
# сплошной CGNAT: за одним адресом сидят сотни человек. Строгий лимит на 15
# запросов сломал бы кнопку «Показать на примере» у заметной доли посетителей
# в первую же минуту наплыва — при том, что демо после пула в памяти стоит
# копейки. Строгий лимит нужен только там, где мы ходим на чужой сервер
# (/api/predict). Счётчики у бакетов раздельные: демо не съедает бюджет
# предикта и наоборот.
DEMO_RATE_LIMIT = _per_process_limit(_env_int("DEMO_RATE_LIMIT_PER_WINDOW", 120), _worker_count())
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


def _proxy_client_ip(request: Request) -> str | None:
    """IP посетителя от своего прокси домена — только при верном ключе."""
    key = os.environ.get("PROXY_KEY", "")
    sent = request.headers.get("x-bagam-proxy-key", "")
    if not key or not hmac.compare_digest(sent.encode(), key.encode()):
        return None
    try:
        return str(ipaddress.ip_address(request.headers.get("x-bagam-client-ip", "").strip()))
    except ValueError:
        return None


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

    Запрос через свой домен (Cloudflare Worker, docs/domain-worker.js)
    приходит с IP Cloudflare — правый элемент XFF общий для всех посетителей
    домена. Воркер кладёт настоящий IP в X-Bagam-Client-IP вместе с ключом
    PROXY_KEY; без совпадения ключа заголовок игнорируется.
    """
    proxied = _proxy_client_ip(request)
    if proxied:
        return proxied
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
# Челлендж лечится сменой сессии, а не ожиданием: повторять можно сразу,
# а не через полминуты, как при перегрузе.
_CHALLENGE_RETRY_AFTER = "5"
# Один текст на любой отказ источника: фронт показывает detail любого 503 как есть.
_SOURCE_DETAIL = "Источник временно не отдаёт объявление, попробуй ещё раз"


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
    except ChallengeBlocked:
        # Источник закрылся anti-bot челленджем (SafeLine, HTTP 468) и не отдал
        # страницу ни на одной попытке. Это НЕ наша внутренняя ошибка, поэтому
        # не 502: 503 + Retry-After честно говорит «внешний источник временно
        # не пускает, повтори», и позволяет смоуку отличить это от поломки
        # сервиса. Ветка стоит ВЫШЕ RuntimeError — ChallengeBlocked его подкласс.
        metrics.bump("predict_challenge")
        logger.warning("predict: источник закрыт anti-bot челленджем", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=_SOURCE_DETAIL,
            headers={"Retry-After": _CHALLENGE_RETRY_AFTER},
        ) from None
    except ListingNotFound as exc:
        # krisha ответила 404 — объявления нет. Не наш сбой и не отказ источника:
        # честный 404 с текстом, который фронт и бот показывают как есть
        # (.kiro/specs/predict-edge-listings). Ветка выше RuntimeError.
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except SourceUnavailable:
        # Страницы нет ни на одной попытке и без SafeLine: таймауты, 5xx,
        # троттлинг, 403 krisha. Тоже внешний отказ, а не наш сбой — до
        # 2026-09-23 он уезжал в 502 (.kiro/specs/safeline-468, §7). Ветка выше
        # RuntimeError и ниже ChallengeBlocked — порядок наследования.
        metrics.bump("predict_source_unavailable")
        logger.warning("predict: источник не отдал объявление", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=_SOURCE_DETAIL,
            headers={"Retry-After": _CHALLENGE_RETRY_AFTER},
        ) from None
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


def _demo_pool(db_path: pathlib.Path | None = None) -> list[tuple[int, str]]:
    # DB_PATH читается в момент вызова, а не при определении: тесты его подменяют
    with get_conn(db_path or DB_PATH) as conn:
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
def demo(request: Request, deal: Literal["prodazha", "arenda"] = "prodazha") -> DemoResponse:
    """URL живого активного объявления для кнопки «Показать на примере»:
    продажа по умолчанию, ?deal=arenda — из базы аренды."""
    _check_rate_limit(request, bucket="demo", limit=DEMO_RATE_LIMIT)
    db_path = RENT_DB_PATH if deal == "arenda" else DB_PATH
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="Демо-объявление временно недоступно")
    # продажа — прежний вызов без аргументов (его же греет warmup), аренда — своя база
    producer = (lambda: _demo_pool(db_path)) if deal == "arenda" else _demo_pool
    pool = _demo_pool_cache.get_or_call(str(db_path), producer)
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


# Кэши хранят уже сериализованный и сжатый ответ (static_cache.Asset):
# десятки КБ JSON не перегоняются через json.dumps + gzip на каждый запрос.
# Имена get_stats/compute_rent_stats/heatmap_points берутся в момент вызова —
# тесты подменяют их на модуле.
def _stats_asset() -> static_cache.Asset:
    return _json_asset(get_stats())


def _rent_stats_asset() -> static_cache.Asset:
    return _json_asset(compute_rent_stats())


def _heatmap_asset() -> static_cache.Asset:
    return _json_asset(heatmap_points())


@app.get("/api/stats")
def stats(request: Request) -> Response:
    """Статистика рынка: всего объявлений, ₸/м² по районам, распределение цен."""
    # max-age меньше серверного TTL: на сервере значение живёт 10 минут,
    # у клиента 5 — никто не увидит цифры старше, чем они есть на бэке.
    try:
        asset = _stats_cache.get_or_call("stats", _stats_asset)
    except FileNotFoundError:
        logger.exception("stats: данные недоступны")
        raise HTTPException(status_code=503, detail="Статистика временно недоступна") from None
    return _cacheable_json(request, asset, max_age=300)


_rent_stats_cache = TTLCache(ttl=STATS_CACHE_TTL, stale_ttl=3600, maxsize=2)


@app.get("/api/stats/rent")
def rent_stats(request: Request) -> Response:
    """Рынок аренды: медианы ₸/мес по районам и комнатам, распределение, доходность районов."""
    try:
        asset = _rent_stats_cache.get_or_call("rent", _rent_stats_asset)
    except FileNotFoundError:
        logger.warning("stats/rent: база аренды недоступна")
        raise HTTPException(status_code=503, detail="Статистика аренды временно недоступна") from None
    return _cacheable_json(request, asset, max_age=300)


_heatmap_cache = TTLCache(ttl=STATS_CACHE_TTL, stale_ttl=3600, maxsize=2)


@app.get("/api/heatmap")
def heatmap(request: Request) -> Response:
    """Сетка ₸/м² для карты: ячейки ~400 м по активным лотам с координатами."""
    try:
        asset = _heatmap_cache.get_or_call("heatmap", _heatmap_asset)
    except FileNotFoundError:
        logger.exception("heatmap: база недоступна")
        raise HTTPException(status_code=503, detail="Карта временно недоступна") from None
    return _cacheable_json(request, asset, max_age=300)


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
    if fcntl is None:
        # Windows (локальная разработка): uvicorn запускают одним процессом —
        # делить подготовку не с кем (.kiro/specs/windows-local-dev).
        yield
        return
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
    _refresh_live_pages()
    _start_session_warmup()
    _start_webhook_setup()


def _start_webhook_setup() -> threading.Thread:
    """Регистрация webhook — фоном, а не в startup.

    uvicorn открывает порт только после startup, а setup_webhook при
    недоступном Telegram крутит 3 попытки по ~30 с: сайт ~100 с не отвечал
    вовсе. Бот без webhook подождёт — сайт ждать не должен; если регистрация
    не удалась, её повторит самолечение в webhook_status (/api/health).
    """
    thread = threading.Thread(target=bot.setup_webhook, name="tg-webhook-setup", daemon=True)
    thread.start()
    return thread


# Сколько лотов из демо-пула пробовать, прогревая сессию krisha: первый мог
# успеть уйти с сайта (404) — тогда второй.
SESSION_WARMUP_TRIES = 2


def _start_session_warmup() -> None:
    """Фоном прогреть липкую сессию krisha — не заставлять платить первого человека.

    После рестарта хранилище кук пустое, и первая сессия тянет жребий SafeLine;
    с IP Hugging Face он часто проигрышный — смоук после выката b999257 прошёл
    предикт только с 3-й из 3 попыток (.kiro/specs/krisha-session-warmup).
    Поток daemon: готовность сервиса не ждёт. KRISHA_SESSION_WARMUP=0 —
    выключить (тесты, герметичный e2e); нет базы/пула — пропуск.
    """
    if os.environ.get("KRISHA_SESSION_WARMUP", "1") == "0":
        return
    try:
        pool = _demo_pool() if DB_PATH.exists() else []
    except Exception:  # noqa: BLE001 — без пула просто не греем
        logger.warning("прогрев сессии: пул демо-лотов недоступен", exc_info=True)
        return
    if not pool:
        return
    urls = [url for _, url in random.sample(pool, min(SESSION_WARMUP_TRIES, len(pool)))]

    def warm() -> None:
        for url in urls:
            if predict_gate.warm_session(url):
                logger.info("krisha: сессия прогрета (%s)", url)
                return
        logger.warning("krisha: прогреть сессию не удалось — первый запрос потянет жребий сам")

    threading.Thread(target=warm, name="krisha-session-warmup", daemon=True).start()


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
    # Состояние бота (подписки, слежка, статистика, лог предиктов) живёт в
    # приватном репо данных, в образе его нет — забираем до первого запроса.
    if os.environ.get("KRISHA_STATE_PULL", "1") != "0":
        from krisha.subscriptions import pull_state

        pull_state()
    # База и веса модели не хранятся в git — при старте скачиваем их из
    # релизов приватного репо данных (issue #74).
    db_release.ensure_db()
    db_release.ensure_models()
    # Аренда: своя база (рынок, аналоги, история цен арендных лотов) и модель
    db_release.ensure_rent_db()
    db_release.ensure_rent_models()
    if DB_PATH.exists():
        # Скачанная база могла не проходить init_db: догоняем миграции
        # и индексы (idx_listings_fingerprint для проверки дублей).
        from krisha.db import init_db

        # Тот же путь, что проверяли строкой выше: без аргумента init_db берёт
        # свой умолчательный DB_PATH, и проверка с действием могли смотреть в
        # разные файлы — тест с подменённым DB_PATH так создавал data/krisha.db
        # прямо в рабочей копии (.kiro/specs/test-state-isolation).
        init_db(DB_PATH)
    if RENT_DB_PATH.exists():
        from krisha.db import init_db

        init_db(RENT_DB_PATH)


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
        if RENT_MODEL_PATH.exists():
            load_model("arenda")
            load_interval_models("arenda")
            load_spatial_ref(RENT_SPATIAL_REF_PATH)
        load_poi_index()
        # Статистика использования: иначе её json с диска читает первый же
        # посетитель — под глобальным локом и ровно в момент наплыва.
        usage.warm()
        # Агрегаты страниц: первый посетитель после рестарта иначе платит за
        # полный пересчёт по базе (а при наплыве он платит не один).
        if DB_PATH.exists():
            warmups = (
                ("stats", _stats_cache, "stats", _stats_asset),
                ("heatmap", _heatmap_cache, "heatmap", _heatmap_asset),
                ("demo", _demo_pool_cache, str(DB_PATH), _demo_pool),
            )
            for name, cache, key, producer in warmups:
                try:
                    cache.get_or_call(key, producer)
                except Exception:  # noqa: BLE001, PERF203 — прогрев не критичен
                    logger.warning("warmup: %s не прогрелся", name, exc_info=True)
        if RENT_DB_PATH.exists():
            try:
                _rent_stats_cache.get_or_call("rent", _rent_stats_asset)
            except Exception:  # noqa: BLE001 — прогрев не критичен
                logger.warning("warmup: stats/rent не прогрелся", exc_info=True)
        logger.info("runtime caches warmed up")
    except Exception:  # noqa: BLE001 — warmup не должен валить запуск Space
        logger.warning("runtime warmup failed", exc_info=True)


# ---------------------------------------------------------------- статика
# Всё текстовое (html/css/js) читается и жмётся ОДИН раз при старте и живёт в
# памяти процесса: см. krisha.api.static_cache — там же цифры замера, ради
# которых это сделано (главная: 361 rps с gzip против 706 без — половина CPU
# уходила на повторное сжатие одного и того же файла).
HTML_CACHE_CONTROL = "no-cache"  # не «не кэшировать», а «спроси ETag»
# Ассеты (css, js, картинки, шрифты): страницы ссылаются на них с ?v=<хэш
# содержимого> (static_cache.build_site) — такой URL меняется вместе с файлом,
# и его держим у браузера год. Раньше css/js шли с no-cache (блокирующий CSS
# перепроверялся на каждом переходе), а svg/webp — immutable без версии:
# поменянный skyline.svg вернувшиеся пользователи не видели. Без ?v= (или со
# старой версией) — no-cache: браузер спросит ETag.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
ASSET_REVALIDATE_CACHE_CONTROL = "no-cache"
PRECOMPRESS_SUFFIXES = (".html", ".css", ".js", ".mjs", ".json", ".webmanifest", ".svg")
# Адрес сайта, захардкоженный в страницах (canonical, og:url, og:image).
# Если задан PUBLIC_BASE_URL, при сборке кэша он подменяет эти адреса — и
# страницы, и sitemap/robots объявляют один и тот же домен.
SITE_ORIGIN = "https://bagam.info"
HTML_SITE_ORIGINS = (SITE_ORIGIN, "https://dex719-krisha-fair-price.hf.space")
_ASSETS: dict[str, static_cache.Asset] = {}
_VERSIONS: dict[str, str] = {}
# HTML после build_site (версии ссылок, адрес сайта) — основа, на которую
# _render_pages кладёт живые цифры, JSON-LD и счётчики.
_BASE_HTML: dict[str, str] = {}
# Ключи _ASSETS, которых нет среди файлов static/: отдают их свои маршруты,
# /static/... их не отдаёт (_CachedStatic отвечает 404 на «?» в имени).
STATS_RENT_ASSET = "stats.html?mode=rent"
LLMS_ASSET = "/llms.txt"
ANALYTICS_JS = "js/analytics.js"


@dataclasses.dataclass(frozen=True)
class _LiveSnapshot:
    """Данные для разметки страниц: ответы /api/stats и /api/stats/rent и тексты [data-l]."""

    stats: dict | None
    rent: dict | None
    values: dict[str, str]


_LIVE: _LiveSnapshot | None = None


def _public_origin() -> str | None:
    return (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/") or None


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
    global _ASSETS, _VERSIONS, _BASE_HTML, _ANALYTICS, _ACTIVE_CSP
    site = static_cache.build_site(
        STATIC_DIR, _asset_names(), origins=HTML_SITE_ORIGINS, public_origin=_public_origin()
    )
    _ANALYTICS = site_analytics.from_env()
    _ACTIVE_CSP = site_analytics.extend_csp(CSP, site_analytics.csp_sources(_ANALYTICS))
    _BASE_HTML = {
        name: asset.raw.decode("utf-8") for name, asset in site.assets.items() if name.endswith(".html")
    }
    _ASSETS, _VERSIONS = site.assets, site.versions
    _render_pages(_LIVE)


def _html_asset(text: str) -> static_cache.Asset:
    return static_cache.asset_from_bytes(text.encode("utf-8"), "text/html; charset=utf-8")


def _render_pages(live: _LiveSnapshot | None) -> None:
    """Страницы из _BASE_HTML + живые цифры, JSON-LD и счётчики → _ASSETS.

    Без снимка (на импорте, до прогрева) — только то, что не зависит от
    данных: FAQ-разметка, счётчики, коды подтверждения. См. live_pages.
    """
    global _ASSETS
    values = live.values if live else {}
    total = (live.stats or {}).get("total_listings") if live else None
    total = int(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None
    version = _VERSIONS.get(ANALYTICS_JS)
    counters = site_analytics.head_snippet(
        _ANALYTICS, f"/static/{ANALYTICS_JS}" + (f"?v={version}" if version else "")
    )
    rendered: dict[str, str] = {}
    for name, page in _BASE_HTML.items():
        if values:
            page = live_pages.fill_live(page, values, total)
        if name == "index.html":
            if live:
                page = live_pages.district_bars(page, live.stats)
            page = live_pages.inject_head(page, site_analytics.verification_meta(_ANALYTICS))
        faq = live_pages.faq_json_ld(page)
        if faq:
            page = live_pages.inject_head(page, faq)
        page = live_pages.inject_head(page, counters)
        if name == "stats.html":
            stats, rent = (live.stats, live.rent) if live else (None, None)
            rendered[STATS_RENT_ASSET] = live_pages.rent_variant(
                live_pages.with_market_noscript(page, live_pages.market_noscript(stats, rent, rent_first=True))
            )
            page = live_pages.with_market_noscript(page, live_pages.market_noscript(stats, rent))
        rendered[name] = page
    assets = dict(_ASSETS)
    assets.update({name: _html_asset(page) for name, page in rendered.items()})
    llms = live_pages.llms_txt(
        _site_base_url(), values, live.stats if live else None, live.rent if live else None
    )
    assets[LLMS_ASSET] = static_cache.asset_from_bytes(llms.encode("utf-8"), "text/plain; charset=utf-8")
    # одно присваивание: параллельный запрос видит либо старый набор, либо новый
    _ASSETS = assets


def _cached_json(cache: TTLCache, key: str, producer) -> dict | None:
    try:
        data = json.loads(cache.get_or_call(key, producer).raw)
    except Exception:  # noqa: BLE001 — нет базы или данных: страница обойдётся без них
        logger.warning("live pages: %s недоступна", key, exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def _live_snapshot() -> _LiveSnapshot:
    """Те же данные, что отдают /api/stats, /api/stats/rent и /api/health (без похода в Telegram).

    Продажа — и без базы: get_stats() тогда отдаёт снимок models/stats.json, как и /api/stats.
    """
    stats = _cached_json(_stats_cache, "stats", _stats_asset)
    rent = _cached_json(_rent_stats_cache, "rent", _rent_stats_asset) if RENT_DB_PATH.exists() else None
    health = {
        "model_error_pct": _model_error_pct(),
        "model_median_error_pct": _model_metric_pct("mdape"),
        "model_error_ci_pct": _model_error_ci_pct(),
        "model_r2": _model_r2(),
        "model_mae": _model_mae(),
        "rent_model_error_pct": _rent_model_error_pct(),
    }
    return _LiveSnapshot(stats=stats, rent=rent, values=live_pages.live_values(stats, health))


def _refresh_live_pages() -> None:
    """Живые цифры в разметку страниц — после прогрева кэшей статистики.

    Процесс перезапускается после каждого сбора данных и переобучения, так
    что снимок в разметке не старше суток. Fail-soft: без данных страницы
    остаются такими, как лежат в файлах. KRISHA_LIVE_PAGES=0 — выключить
    (тесты и герметичный e2e: разметка как в файлах, без чисел из базы).
    """
    global _LIVE
    if os.environ.get("KRISHA_LIVE_PAGES", "1") == "0":
        return
    try:
        _LIVE = _live_snapshot()
        _render_pages(_LIVE)
        logger.info("live pages: в разметке %d живых значений", len(_LIVE.values))
    except Exception:  # noqa: BLE001 — сайт без снимка лучше, чем сайт, который не стартовал
        logger.warning("live pages: снимок не собран", exc_info=True)


def _static_cache_control(name: str, scope) -> str:
    """immutable — только если ?v= совпадает с версией файла в этом процессе.

    Чужая версия (страница из кэша браузера после деплоя, второй воркер ещё
    на старом образе) — no-cache: иначе под новым URL на год застряли бы
    старые байты.
    """
    query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    version = (query.get("v") or [None])[0]
    if version and version == _VERSIONS.get(name):
        return IMMUTABLE_CACHE_CONTROL
    return ASSET_REVALIDATE_CACHE_CONTROL


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
    """Браузеру — оформленная страница, любому клиенту API — обычный JSON.

    Адрес страницы со слэшем на конце (/about/) — 301 на адрес без него
    (redirect_slashes выключен, см. начало модуля).
    """
    path = request.url.path
    if path != "/" and path.endswith("/") and path.rstrip("/") in _PAGE_PATHS:
        query = request.url.query
        target = path.rstrip("/") + (f"?{query}" if query else "")
        return _apply_security_headers(RedirectResponse(target, status_code=301))
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
# issue #190 §2.7 (из RFC #178 PR 1, без PostgreSQL): два пробника без
# внешних зависимостей. /api/health остаётся публичным статусом (метрики,
# возраст данных, webhook — с походом в Telegram); оркестратору и keepalive
# нужны ответы, которые не зависят ни от Telegram, ни от GitHub.
@app.api_route("/livez", methods=["GET", "HEAD"], include_in_schema=False)
async def livez() -> Response:
    """Процесс жив и event loop крутится. Больше ничего не проверяет."""
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})


@app.api_route("/readyz", methods=["GET", "HEAD"], include_in_schema=False)
async def readyz() -> Response:
    """Готов отдавать вердикты: модель и база на диске, страницы в памяти.

    503 — контейнер поднялся, но артефакты ещё качаются (KRISHA_DB_AUTO) или
    их нет. Telegram/GitHub/сеть не трогаем намеренно: их недоступность не
    делает оценку по ссылке невозможной (комментарий к #178, п.2).
    """
    checks = {
        "model": MODEL_PATH.exists(),
        "db": DB_PATH.exists(),
        "assets": bool(_ASSETS) or not STATIC_DIR.exists(),
    }
    ready = all(checks.values())
    return JSONResponse(
        {"status": "ok" if ready else "not_ready", "checks": checks, "revision": BUILD_REVISION},
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
    )


# issue #190 §2.6: до этого оба URL отдавали 404 — сайт не просился в индекс.
# (путь, файл страницы, на странице данные рынка). /rent здесь нет: «Аренда»
# стала режимом «Рынка», а /rent — 301 туда. У режима аренды свой адрес в
# sitemap — /stats?mode=rent: свои title и canonical (live_pages.rent_variant).
_PUBLIC_PAGES: tuple[tuple[str, str, bool], ...] = (
    ("/", "index.html", True),
    ("/stats", "stats.html", True),
    ("/stats?mode=rent", "stats.html", True),
    ("/about", "about.html", False),
    ("/bot", "bot.html", False),
    ("/privacy", "privacy.html", False),
    ("/terms", "terms.html", False),
)
# Адреса HTML-страниц: им со слэшем на конце — 301 на адрес без него.
_PAGE_PATHS = frozenset(p for p, _, _ in _PUBLIC_PAGES if "?" not in p) | {"/rent"}


def _site_base_url(request: Request | None = None) -> str:
    """Адрес сайта для sitemap/robots — тот же, что в canonical страниц.

    PUBLIC_BASE_URL, иначе адрес, захардкоженный в HTML (SITE_ORIGIN). Раньше
    без PUBLIC_BASE_URL брался домен Space (SPACE_HOST), и sitemap объявлял
    dex719-…hf.space, а canonical страниц — bagam.info.
    """
    return _public_origin() or SITE_ORIGIN


@app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
async def robots_txt(request: Request) -> Response:
    # /api/stats (с /api/stats/rent) и /api/health открыты: из них скрипт рисует
    # цифры «Рынка» и точность модели, и поисковик, который исполняет JS, без
    # них видел пустые таблицы. В индекс JSON не попадёт: X-Robots-Tag: noindex.
    # Самое длинное совпадение побеждает и у Google, и у Яндекса, поэтому Allow
    # пересиливает Disallow: /api/ при любом порядке строк.
    # Clean-param — только для Яндекса: метки рекламы и рассылок не плодят дубли.
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /api/stats\n"
        "Allow: /api/health\n"
        "Disallow: /api/\n"
        "Disallow: /tg/\n"
        "Disallow: /docs\n"
        "Disallow: /redoc\n"
        "Disallow: /openapi.json\n"
        "Clean-param: utm_source&utm_medium&utm_campaign&utm_content&utm_term&yclid&gclid&fbclid\n"
        "\n"
        f"Sitemap: {_site_base_url(request)}/sitemap.xml\n"
    )
    return Response(body, media_type="text/plain; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=86400"})


def _file_date(name: str) -> datetime | None:
    try:
        return datetime.fromtimestamp((STATIC_DIR / name).stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _sitemap_lastmod(name: str, has_data: bool, data_at: datetime | None) -> str | None:
    """Дата изменения страницы: mtime файла, у страниц с данными рынка — не
    раньше последнего прохода сборщика. Раньше всегда стояло «сегодня» —
    поисковик учится не верить такому lastmod."""
    dates = [d for d in (_file_date(name), data_at if has_data else None) if d is not None]
    return max(dates).date().isoformat() if dates else None


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_xml(request: Request) -> Response:
    # def, не async: момент сбора читается из базы (через кэш свежести)
    base = _site_base_url(request)
    data_at = _freshness_cache.get_or_call(("observed", str(DB_PATH)), _data_observed_at)
    urls = []
    for path, name, has_data in _PUBLIC_PAGES:
        lastmod = _sitemap_lastmod(name, has_data, data_at)
        urls.append(
            f"<url><loc>{base}{path}</loc>"
            + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "")
            + f"<changefreq>{'daily' if has_data else 'monthly'}</changefreq></url>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{''.join(urls)}</urlset>"
    )
    return Response(body, media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.api_route("/rent", methods=["GET", "HEAD"], include_in_schema=False)
async def rent_page(request: Request) -> Response:
    """«Аренда» слилась с «Рынком»: 301 на /stats?mode=rent.

    Остальные параметры переносятся (/rent?url=X → /stats?mode=rent&url=X),
    свой mode у запроса не перебивает аренду.
    """
    params = [("mode", "rent")] + [
        (key, value) for key, value in request.query_params.multi_items() if key != "mode"
    ]
    return RedirectResponse(f"/stats?{urlencode(params)}", status_code=301)


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def index(request: Request) -> Response:
    if request.method == "GET":
        usage.record_event("site")
    return _asset_response(request, "index.html")


@app.api_route("/stats", methods=["GET", "HEAD"], include_in_schema=False)
async def stats_page(request: Request) -> Response:
    if request.method == "GET":
        usage.record_event("site")
    # ?mode=rent — та же страница со своими title и canonical (live_pages.rent_variant)
    rent = request.query_params.get("mode") == "rent" and STATS_RENT_ASSET in _ASSETS
    return _asset_response(request, STATS_RENT_ASSET if rent else "stats.html")


@app.api_route("/llms.txt", methods=["GET", "HEAD"], include_in_schema=False)
async def llms_txt(request: Request) -> Response:
    """Справка о сервисе для нейросетей (llmstxt.org): что это, главные цифры, страницы."""
    return _asset_response(request, LLMS_ASSET, cache_control="public, max-age=3600")


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon_ico() -> Response:
    """Браузеры и Яндекс просят /favicon.ico сами, даже при SVG-иконке в <head>."""
    return FileResponse(
        STATIC_DIR / "favicon.ico", media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.api_route("/about", methods=["GET", "HEAD"], include_in_schema=False)
async def about_page(request: Request) -> Response:
    if request.method == "GET":
        usage.record_event("site")
    return _asset_response(request, "about.html")


def _static_page(name: str):
    """Обработчик простой страницы: та же отдача и тот же учёт визита, что у /about."""
    async def page(request: Request) -> Response:
        if request.method == "GET":
            usage.record_event("site")
        return _asset_response(request, name)
    return page


# Бот и документы — те же статичные страницы на общем design.css
for _path, _name in (("/bot", "bot.html"), ("/privacy", "privacy.html"), ("/terms", "terms.html")):
    app.add_api_route(_path, _static_page(_name), methods=["GET", "HEAD"], include_in_schema=False)


class _CachedStatic(StaticFiles):
    """Текст — из предсжатой памяти, бинарь — обычной отдачей файла.
    Cache-Control у обоих — по версии в URL (_static_cache_control)."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        # StaticFiles отдаёт путь в разделителях ОС (на Windows — «\»)
        name = pathlib.PurePath(path).as_posix().lstrip("/")
        # страницы живут по своим адресам (/about), копия /static/about.html —
        # дубль для поисковика, к тому же без живых цифр и счётчиков. «?» в
        # имени — это %3F в адресе: служебные ключи вроде stats.html?mode=rent
        # отдают только свои маршруты.
        if name.lower().endswith(".html") or "?" in name:
            raise HTTPException(status_code=404)
        cache_control = _static_cache_control(name, scope)
        if name in _ASSETS and scope.get("method") in ("GET", "HEAD"):
            return _asset_response(Request(scope), name, cache_control=cache_control)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = cache_control
        return response

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        path = str(getattr(response, "path", ""))
        media_type = _BINARY_MEDIA_TYPES.get(pathlib.Path(path).suffix.lower())
        if media_type:
            response.headers["Content-Type"] = media_type
        return response


_build_assets()

if STATIC_DIR.exists():
    app.mount("/static", _CachedStatic(directory=STATIC_DIR), name="static")
