"""FastAPI-приложение: /api/predict, /api/health + статичный фронт.

Запуск: `uvicorn krisha.api.app:app --reload`
"""

import hmac
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from krisha import __version__, bot, db_release, usage
from krisha.api.schemas import (
    DemoResponse,
    FlagsResponse,
    HealthResponse,
    PredictRequest,
    PredictResponse,
)
from krisha.config import DB_PATH, MODEL_PATH, ROOT_DIR
from krisha.db import get_conn
from krisha.predict import predict_from_url
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
# Известное ограничение (осознанно принято): проверка идёт только по
# заголовку Content-Length. Запрос с Transfer-Encoding: chunked и без
# Content-Length пройдёт мимо этой проверки — чтобы ловить и его, нужно
# читать тело потоково с подсчётом байт, что требует отдельного решения
# (например ASGI-обёртки над request.stream()). Для нашего профиля риска
# (внутренний API, максимум JSON с одним URL-полем) это принято как
# допустимый компромисс и не реализуется здесь.
MAX_BODY_BYTES = 64 * 1024


def _apply_security_headers(response):
    """Навешивает security-заголовки на любой ответ — и обычный, и ранний
    413/400 из проверки размера тела (см. _security_headers ниже)."""
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.middleware("http")
async def _security_headers(request: Request, call_next):
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
    return _apply_security_headers(response)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # webhook_status() заодно самолечит webhook (не чаще раза в час):
    # keepalive-пинг каждые 6 часов держит бота живым без ручных действий
    return HealthResponse(
        status="ok", model_loaded=MODEL_PATH.exists(), tg_webhook=bot.webhook_status()
    )


# Анти-спам: скользящее окно запросов на IP (живём в одном процессе — хватает)
RATE_LIMIT = 15  # запросов
RATE_WINDOW_S = 60.0
_rate: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # ВНИМАНИЕ: X-Forwarded-For легко подделать, поэтому это НЕ строгая защита —
    # лимит можно обойти сменой заголовка. За доверенным прокси сюда
    # стоит подставлять реальный client IP. Пока — best-effort анти-спам.
    ip = (request.client.host if request.client else None) or "?"
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip()
    return ip


MAX_RATE_KEYS = 10_000  # потолок против разрастания памяти (в т.ч. от подделки XFF)


def _check_rate_limit(request: Request) -> None:
    ip = _client_ip(request)
    now = time.monotonic()
    # Вытесняем протухшие ключи, чтобы словарь не рос бесконечно.
    if len(_rate) > MAX_RATE_KEYS:
        for key in [k for k, dq in _rate.items() if not dq or now - dq[-1] > RATE_WINDOW_S]:
            del _rate[key]
    q = _rate[ip]
    while q and now - q[0] > RATE_WINDOW_S:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много запросов, подожди минуту")
    q.append(now)


@app.post("/api/predict", response_model=PredictResponse)
def predict(req: PredictRequest, request: Request) -> PredictResponse:
    _check_rate_limit(request)
    try:
        # flags_live=False: отвечаем сразу, LLM-флаги фронт догружает отдельно
        result = predict_from_url(req.url, flags_live=False)
    except ValueError as exc:
        # 422 — пользовательская валидация URL, текст безопасен и полезен
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError:
        # детали (пути и т.п.) — в лог, наружу обобщённо
        logger.exception("predict: модель/файл недоступны")
        raise HTTPException(status_code=503, detail="Сервис временно недоступен") from None
    except RuntimeError:
        logger.exception("predict: ошибка обработки объявления")
        raise HTTPException(status_code=502, detail="Не удалось обработать объявление") from None
    usage.record_event("predict")
    return PredictResponse(**result)


@app.get("/api/flags/{listing_id}", response_model=FlagsResponse)
def flags(listing_id: int, request: Request) -> FlagsResponse:
    """Догрузка LLM-бейджей: из кэша или один живой запрос к Gemini."""
    _check_rate_limit(request)
    from krisha.llm_flags import build_text_flags

    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, description FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    # max_retries=1: ровно один живой запрос без sleep-бэкоффов — иначе
    # ретраи Gemini держали бы поток тредпула до минут на один запрос
    text_flags = build_text_flags(
        {"id": row["id"], "description": row["description"]}, max_retries=1
    )
    return FlagsResponse(listing_id=listing_id, text_flags=text_flags)


@app.get("/api/demo", response_model=DemoResponse)
def demo(request: Request) -> DemoResponse:
    """URL живого активного объявления для кнопки «Показать на примере»."""
    _check_rate_limit(request)
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Демо-объявление временно недоступно")
    with get_conn(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT id, url
            FROM listings
            WHERE is_active = 1
              AND url IS NOT NULL
              AND url LIKE '%krisha.kz/a/show/%'
            ORDER BY RANDOM()
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=503, detail="Демо-объявление временно недоступно")
    return DemoResponse(listing_id=int(row["id"]), url=str(row["url"]))


_stats_cache: dict = {"data": None, "ts": 0.0}
STATS_CACHE_TTL = 600  # секунд


@app.get("/api/stats")
def stats() -> dict:
    """Статистика рынка: всего объявлений, ₸/м² по районам, распределение цен."""
    now = time.monotonic()
    if _stats_cache["data"] is not None and now - _stats_cache["ts"] < STATS_CACHE_TTL:
        return _stats_cache["data"]
    try:
        data = get_stats()
    except FileNotFoundError:
        logger.exception("stats: данные недоступны")
        raise HTTPException(status_code=503, detail="Статистика временно недоступна") from None
    _stats_cache.update(data=data, ts=now)
    return data


_heatmap_cache: dict = {"data": None, "ts": 0.0}


@app.get("/api/heatmap")
def heatmap() -> list[dict]:
    """Сетка ₸/м² для карты: ячейки ~400 м по активным лотам с координатами."""
    now = time.monotonic()
    if _heatmap_cache["data"] is not None and now - _heatmap_cache["ts"] < STATS_CACHE_TTL:
        return _heatmap_cache["data"]
    try:
        data = heatmap_points()
    except FileNotFoundError:
        logger.exception("heatmap: база недоступна")
        raise HTTPException(status_code=503, detail="Карта временно недоступна") from None
    _heatmap_cache.update(data=data, ts=now)
    return data


_forecast_cache: dict = {"data": None, "ts": 0.0}


@app.get("/api/forecast")
def forecast() -> dict:
    """Прогноз ₸/м² на 3–6 месяцев: линейный тренд недельных медиан по районам."""
    now = time.monotonic()
    if _forecast_cache["data"] is not None and now - _forecast_cache["ts"] < STATS_CACHE_TTL:
        return _forecast_cache["data"]
    from krisha.forecast import build_forecast

    try:
        data = build_forecast()
    except FileNotFoundError:
        logger.exception("forecast: база недоступна")
        raise HTTPException(status_code=503, detail="Прогноз временно недоступен") from None
    _forecast_cache.update(data=data, ts=now)
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
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        x_telegram_bot_api_secret_token, bot.webhook_secret(token)
    ):
        raise HTTPException(status_code=403)
    update_id = update.get("update_id")
    if isinstance(update_id, int):
        if update_id in _SEEN_UPDATE_IDS:
            return {"ok": True}
        _SEEN_UPDATE_IDS.append(update_id)
    background_tasks.add_task(_process_tg_update, update)
    return {"ok": True}


def _startup() -> None:
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
    _warmup_runtime_caches()
    bot.setup_webhook()


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
        logger.info("runtime caches warmed up")
    except Exception:  # noqa: BLE001 — warmup не должен валить запуск Space
        logger.warning("runtime warmup failed", exc_info=True)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    usage.record_event("site")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/stats", include_in_schema=False)
def stats_page() -> FileResponse:
    usage.record_event("site")
    return FileResponse(STATIC_DIR / "stats.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
