"""Telegram-бот: оценка справедливой цены квартиры по ссылке на объявление.

Работает через webhook на том же FastAPI-приложении, что и сайт, —
отдельный сервер не нужен. Telegram сам присылает апдейты
на `POST /tg/webhook`, бот отвечает через Bot API.

Настройка (env):
- TELEGRAM_BOT_TOKEN — токен от @BotFather (без него бот выключен, приложение работает как обычно)
- PUBLIC_BASE_URL / RAILWAY_PUBLIC_DOMAIN — публичный адрес для регистрации webhook
  (на Railway RAILWAY_PUBLIC_DOMAIN выставляется автоматически)
"""

import hashlib
import html
import logging
import os
from typing import Any

import httpx

from krisha.predict import KRISHA_URL_RE, predict_from_url

logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org"
WEBHOOK_PATH = "/tg/webhook"
CAPTION_LIMIT = 1024  # лимит Telegram на подпись к фото

FEATURE_RU = {
    "area": "Площадь",
    "rooms": "Комнаты",
    "floor": "Этаж",
    "total_floors": "Этажность",
    "floor_ratio": "Положение по этажам",
    "is_first_floor": "Первый этаж",
    "is_last_floor": "Последний этаж",
    "year_built": "Год постройки",
    "building_age": "Возраст дома",
    "ceiling": "Потолки",
    "lat": "Широта",
    "lon": "Долгота",
    "dist_center_km": "Расстояние до центра",
    "district": "Район",
    "microdistrict": "Микрорайон",
    "building_type": "Тип дома",
    "complex_name": "Жилой комплекс",
    "user_type": "Продавец",
    "category": "Категория",
    "photos_count": "Кол-во фото",
    "district_ppsm": "Уровень цен района",
    "microdistrict_ppsm": "Уровень цен микрорайона",
    "is_new_building": "Новостройка",
    "renovation": "Ремонт",
    "toilet": "Санузел",
    "furniture": "Мебель",
    "parking": "Парковка",
    "balcony": "Балкон",
    "has_security_guard": "Охрана",
    "has_intercom": "Домофон",
    "has_video_surveillance": "Видеонаблюдение",
    "security_count": "Опции безопасности",
    "housing_class": "Класс жилья",
    "developer": "Застройщик",
    "completion_year": "Год сдачи ЖК",
    "apartments_count": "Размер ЖК",
    "dist_metro_km": "Близость метро",
    "dist_school_km": "Близость школы",
    "dist_kindergarten_km": "Близость детсада",
    "dist_park_km": "Близость парка",
    "dist_supermarket_km": "Близость супермаркета",
    "dist_bus_stop_km": "Близость остановки",
    "dist_big_road_km": "Близость магистрали",
    "dist_industrial_km": "Близость промзоны",
    "walk_score": "Пешая доступность",
}

VERDICT_RU = {
    "GOOD_DEAL": "🟢 Выгодно — дешевле оценки",
    "FAIR": "🟡 Справедливая цена",
    "OVERPRICED": "🔴 Переплата",
}

HELP_TEXT = (
    "🏠 <b>Krisha Fair Price</b>\n\n"
    "Пришли мне ссылку на объявление о продаже квартиры в Алматы "
    "(вида <code>https://krisha.kz/a/show/…</code>) — я оценю справедливую цену "
    "ML-моделью, обученной на тысячах реальных объявлений, и скажу, "
    "выгодно это или переплата.\n\n"
    "Веб-версия: https://krisha-fair-price-production.up.railway.app"
)


def bot_token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or None


def webhook_secret(token: str) -> str:
    """Секрет для проверки, что запрос пришёл именно от Telegram."""
    return hashlib.sha256(f"kfp:{token}".encode()).hexdigest()[:32]


def tg_call(method: str, **payload: Any) -> dict | None:
    """Вызов метода Telegram Bot API. Ошибки логируем, наружу не роняем."""
    token = bot_token()
    if not token:
        return None
    try:
        resp = httpx.post(f"{TG_API}/bot{token}/{method}", json=payload, timeout=15.0)
        data = resp.json()
        if not data.get("ok"):
            logger.warning("Telegram %s: %s", method, data)
        return data
    except httpx.HTTPError as exc:
        logger.warning("Telegram %s failed: %s", method, exc)
        return None


def fmt_tenge(value: float | int) -> str:
    return f"{round(value):,}".replace(",", " ") + " ₸"


def format_reply(result: dict[str, Any]) -> str:
    """Текст ответа бота (HTML) по результату predict_from_url."""
    lines: list[str] = []
    title = result.get("title")
    address = result.get("address")
    if title:
        lines.append(f"🏠 <b>{html.escape(title)}</b>")
    if address:
        lines.append(f"📍 {html.escape(address)}")
    if lines:
        lines.append("")

    actual = result.get("actual_price")
    if actual:
        lines.append(f"💰 Цена в объявлении: <b>{fmt_tenge(actual)}</b>")
    lines.append(f"⚖️ Справедливая цена: <b>{fmt_tenge(result['fair_price'])}</b>")

    verdict = result.get("verdict")
    diff = result.get("diff_pct")
    if verdict:
        line = VERDICT_RU.get(verdict, verdict)
        if diff is not None:
            line += f" ({diff:+.1f}%)"
        lines.append(line)

    factors = result.get("top_factors") or []
    if factors:
        lines.append("")
        lines.append("📊 <b>Что влияет на цену:</b>")
        for f in factors[:5]:
            arrow = "▲" if f["impact"] > 0 else "▼"
            lines.append(f"{arrow} {FEATURE_RU.get(f['feature'], f['feature'])}")

    return "\n".join(lines)


def extract_url(text: str) -> str | None:
    match = KRISHA_URL_RE.search(text)
    return f"https://krisha.kz/a/show/{match.group(1)}" if match else None


def handle_update(update: dict[str, Any]) -> None:
    """Обработка одного апдейта Telegram (текстовые сообщения)."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if text.startswith("/start") or text.startswith("/help"):
        tg_call("sendMessage", chat_id=chat_id, text=HELP_TEXT,
                parse_mode="HTML", disable_web_page_preview=True)
        return

    url = extract_url(text)
    if not url:
        tg_call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                text="Не вижу ссылки на объявление 🤔\nПришли ссылку вида "
                     "<code>https://krisha.kz/a/show/123456789</code>")
        return

    tg_call("sendChatAction", chat_id=chat_id, action="typing")
    try:
        result = predict_from_url(url)
    except FileNotFoundError:
        tg_call("sendMessage", chat_id=chat_id, text="Модель ещё не загружена, попробуй позже 🙏")
        return
    except (ValueError, RuntimeError) as exc:
        tg_call("sendMessage", chat_id=chat_id,
                text=f"Не получилось оценить объявление: {exc}")
        return

    reply = format_reply(result)
    photos = result.get("photos") or []
    sent = None
    if photos:
        caption = reply if len(reply) <= CAPTION_LIMIT else reply[: CAPTION_LIMIT - 1] + "…"
        sent = tg_call("sendPhoto", chat_id=chat_id, photo=photos[0],
                       caption=caption, parse_mode="HTML")
    if not photos or not (sent or {}).get("ok"):
        tg_call("sendMessage", chat_id=chat_id, text=reply,
                parse_mode="HTML", disable_web_page_preview=True)


def public_base_url() -> str | None:
    """Публичный URL приложения: PUBLIC_BASE_URL или домен Railway."""
    explicit = os.environ.get("PUBLIC_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{domain}" if domain else None


def setup_webhook() -> None:
    """Регистрирует webhook при старте приложения (no-op без токена/домена)."""
    token = bot_token()
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN не задан — Telegram-бот выключен")
        return
    base = public_base_url()
    if not base:
        logger.warning("Токен бота есть, но публичный URL неизвестен — webhook не настроен")
        return
    data = tg_call(
        "setWebhook",
        url=f"{base}{WEBHOOK_PATH}",
        secret_token=webhook_secret(token),
        allowed_updates=["message"],
        drop_pending_updates=True,
    )
    if data and data.get("ok"):
        logger.info("Telegram webhook настроен: %s%s", base, WEBHOOK_PATH)
