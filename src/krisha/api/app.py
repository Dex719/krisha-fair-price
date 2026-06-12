"""FastAPI-приложение: /api/predict, /api/health + статичный фронт.

Запуск: `uvicorn krisha.api.app:app --reload`
"""

import logging
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from krisha import bot
from krisha.api.schemas import HealthResponse, PredictRequest, PredictResponse
from krisha.config import MODEL_PATH, ROOT_DIR
from krisha.predict import predict_from_url
from krisha.stats import get_stats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Krisha Fair Price", version="0.1.0")

STATIC_DIR = ROOT_DIR / "static"


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", model_loaded=MODEL_PATH.exists())


@app.post("/api/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    try:
        result = predict_from_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return PredictResponse(**result)


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
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    _stats_cache.update(data=data, ts=now)
    return data


@app.post("/tg/webhook", include_in_schema=False)
def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    """Webhook Telegram-бота. Telegram шлёт сюда апдейты после setWebhook."""
    token = bot.bot_token()
    if not token:
        raise HTTPException(status_code=404)
    if x_telegram_bot_api_secret_token != bot.webhook_secret(token):
        raise HTTPException(status_code=403)
    try:
        bot.handle_update(update)
    except Exception:  # бот не должен ронять вебхук — Telegram будет ретраить
        logger.exception("Ошибка обработки Telegram-апдейта")
    return {"ok": True}


@app.on_event("startup")
def _startup() -> None:
    bot.setup_webhook()


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/stats", include_in_schema=False)
def stats_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "stats.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
