"""Telegram-бот: оценка справедливой цены квартиры по ссылке на объявление.

Работает через webhook на том же FastAPI-приложении, что и сайт, —
отдельный сервер не нужен. Telegram сам присылает апдейты
на `POST /tg/webhook`, бот отвечает через Bot API.

Настройка (env):
- TELEGRAM_BOT_TOKEN — токен от @BotFather (без него бот выключен, приложение работает как обычно)
- PUBLIC_BASE_URL / SPACE_HOST — публичный адрес для регистрации webhook
  (на Hugging Face Spaces SPACE_HOST выставляется автоматически)
"""

import hashlib
import html
import logging
import os
import time
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
    "выгодно это или переплата.\n"
    "Можно и без ссылки: вставь текст объявления — оценю по описанию.\n\n"
    "🔔 /alerts — ежедневные алерты о новых выгодных объявлениях\n"
    "👀 /track <i>ссылка</i> — следить за лотом: пришлю алерт, если цена "
    "изменится или объявление снимут\n"
    "Веб-версия: https://dex719-krisha-fair-price.hf.space"
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

    scam = result.get("scam_risk")
    if scam:
        mark = "🚨" if scam.get("level") == "high" else "⚠️"
        head = "Похоже на мошенничество" if scam.get("level") == "high" else "Будьте осторожны"
        reasons = ", ".join(scam.get("reasons") or [])
        lines.append(f"{mark} <b>{head}:</b> {html.escape(reasons)}")

    reno = result.get("renovation")
    if reno:
        line = f"🛠 Ремонт по фото: <b>{html.escape(reno.get('label') or '')}</b>"
        if reno.get("comment"):
            line += f" — {html.escape(reno['comment'])}"
        lines.append(line)

    factors = result.get("top_factors") or []
    if factors:
        lines.append("")
        lines.append("📊 <b>Почему такая цена</b> (топ-3 фактора):")
        for f in factors[:3]:
            arrow = "▲" if f["impact"] > 0 else "▼"
            name = FEATURE_RU.get(f["feature"], f["feature"])
            line = f"{arrow} {name}"
            pct, tenge = f.get("impact_pct"), f.get("impact_tenge")
            if pct is not None and abs(pct) >= 0.05:
                line += f": {pct:+.1f}%"
                if tenge and abs(tenge) >= 100_000:
                    line += f" ({tenge / 1_000_000:+.1f} млн ₸)"
            lines.append(line)

    analogs = result.get("analogs") or []
    if analogs:
        lines.append("")
        lines.append("🏘 <b>Похожие квартиры:</b>")
        for a in analogs[:3]:
            title = html.escape(a.get("title") or f"{a.get('rooms', '?')}-комн, {a.get('area', '?')} м²")
            lines.append(
                f'• <a href="{html.escape(a["url"])}">{title}</a> — '
                f"{a['price'] / 1_000_000:.1f} млн ₸"
            )

    # Этап 5: бейджи из LLM-анализа описания
    flags = result.get("text_flags") or []
    if flags:
        lines.append("")
        lines.append("📝 <b>Анализ описания:</b>")
        for f in flags:
            mark = "⚠️" if f.get("kind") == "warn" else "✅"
            lines.append(f"{mark} {html.escape(f.get('label', ''))}")

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

    from krisha.usage import record_event

    record_event("bot", user_id=chat_id)

    if text.startswith("/start") or text.startswith("/help"):
        payload: dict[str, Any] = {"chat_id": chat_id, "text": HELP_TEXT,
                                   "parse_mode": "HTML",
                                   "disable_web_page_preview": True}
        base = public_base_url()
        if base and message.get("chat", {}).get("type") == "private":
            # web_app-кнопки Telegram разрешает только в личных чатах
            payload["reply_markup"] = {"inline_keyboard": [[
                {"text": "📱 Открыть приложение", "web_app": {"url": base}}
            ]]}
        tg_call("sendMessage", **payload)
        return

    if text.startswith("/alerts"):
        _handle_alerts_command(chat_id, text)
        return

    if text.startswith(("/track", "/untrack")):
        _handle_track_command(chat_id, text)
        return

    url = extract_url(text)
    if not url:
        _handle_free_text(chat_id, text)
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


NO_URL_HINT = ("Не вижу ссылки на объявление 🤔\nПришли ссылку вида "
               "<code>https://krisha.kz/a/show/123456789</code> — или просто "
               "вставь текст объявления, попробую оценить по описанию.")

_PARSED_RU = {"rooms": "комнат", "area": "площадь", "floor": "этаж",
              "total_floors": "этажность", "year_built": "год постройки",
              "ceiling": "потолки", "district": "район",
              "microdistrict": "микрорайон", "building_type": "тип дома",
              "complex_name": "ЖК", "price": "цена", "address_title": "адрес"}


def _handle_free_text(chat_id: int, text: str) -> None:
    """Не ссылка: пробуем распознать вставленный текст объявления (Gemini)."""
    from krisha.text_parse import MIN_TEXT_LEN, predict_from_text

    if len(text.strip()) < MIN_TEXT_LEN:
        tg_call("sendMessage", chat_id=chat_id, parse_mode="HTML", text=NO_URL_HINT)
        return

    tg_call("sendChatAction", chat_id=chat_id, action="typing")
    try:
        result = predict_from_text(text)
    except FileNotFoundError:
        tg_call("sendMessage", chat_id=chat_id, text="Модель ещё не загружена, попробуй позже 🙏")
        return
    except Exception:  # noqa: BLE001 — свободный текст не должен ронять бота
        logger.exception("text_parse failed")
        result = None

    if result is None:
        tg_call("sendMessage", chat_id=chat_id, parse_mode="HTML", text=NO_URL_HINT)
        return
    if result.get("error") == "no_key_fields":
        tg_call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                text="Похоже на объявление, но не вижу ключевых параметров 🤔\n"
                     "Добавь в текст хотя бы <b>площадь</b> и <b>число комнат</b>.")
        return

    parsed = result.get("parsed_fields") or {}
    known = ", ".join(_PARSED_RU[k] for k in _PARSED_RU if k in parsed)
    header = ("📝 <b>Оценка по тексту</b> — примерная: без фото, точного адреса "
              f"и истории лота.\nРаспознал: {known}.\n\n")
    tg_call("sendMessage", chat_id=chat_id, text=header + format_reply(result),
            parse_mode="HTML", disable_web_page_preview=True)


ALERTS_HELP = (
    "🔔 <b>Алерты на выгодные объявления</b>\n\n"
    "Раз в день после обновления базы я присылаю новые объявления, "
    "которые дешевле оценки модели.\n\n"
    "<code>/alerts_on</code> — подписаться на все выгодные\n"
    "<code>/alerts_on 2к до 45млн бостандыкский</code> — с фильтрами "
    "(комнаты, бюджет, район — в любом порядке, всё опционально)\n"
    "<code>/alerts_off</code> — отписаться"
)


def _handle_alerts_command(chat_id: int, text: str) -> None:
    """Команды /alerts, /alerts_on <фильтры>, /alerts_off."""
    from krisha.subscriptions import (
        describe_filters,
        load_subscriptions,
        parse_filters,
        remove_subscription,
        set_subscription,
    )

    cmd, _, args = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/alerts_on":
        flt = parse_filters(args)
        set_subscription(chat_id, flt)
        tg_call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                text=f"✅ Подписал: <b>{describe_filters(flt)}</b>.\n"
                     "Пришлю новые выгодные лоты после ближайшего обновления базы "
                     "(раз в день утром). Отписаться: /alerts_off")
    elif cmd == "/alerts_off":
        removed = remove_subscription(chat_id)
        tg_call("sendMessage", chat_id=chat_id,
                text="Отписал 👌" if removed else "Ты и не был подписан 🙂")
    else:
        sub = load_subscriptions().get(str(chat_id))
        status = f"\n\nТекущая подписка: <b>{describe_filters(sub)}</b>" if sub else ""
        tg_call("sendMessage", chat_id=chat_id, text=ALERTS_HELP + status, parse_mode="HTML")


TRACK_HELP = (
    "👀 <b>Слежка за объявлениями</b>\n\n"
    "<code>/track https://krisha.kz/a/show/…</code> — следить за лотом: "
    "после каждого обновления базы пришлю алерт, если цена изменилась "
    "или объявление сняли с продажи.\n"
    "<code>/track</code> — список лотов в слежке\n"
    "<code>/untrack https://krisha.kz/a/show/…</code> — перестать следить\n"
    "<code>/untrack all</code> — очистить список"
)


def _tracked_list_text(chat_id: int) -> str:
    from krisha.tracking import list_tracked

    lots = list_tracked(chat_id)
    if not lots:
        return "Ты пока ни за чем не следишь. Пришли /track со ссылкой на объявление."
    lines = ["👀 <b>Слежу для тебя:</b>", ""]
    for lid, state in lots.items():
        title = html.escape(state.get("title") or f"Объявление {lid}")
        price = state.get("price")
        price_txt = f" — {price / 1_000_000:.1f} млн ₸" if price else ""
        lines.append(f'• <a href="https://krisha.kz/a/show/{lid}">{title}</a>{price_txt}')
    return "\n".join(lines)


def _track_listing_meta(listing_id: int) -> tuple[int | None, str | None]:
    """Цена и заголовок лота: из базы, а если лота там нет — с сайта."""
    import sqlite3

    from krisha.config import DB_PATH
    from krisha.db import get_conn, upsert_listing
    from krisha.scraping.client import PoliteClient
    from krisha.scraping.detail_parser import parse_detail

    try:
        with get_conn(DB_PATH) as conn:
            row = conn.execute(
                "SELECT price, title FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()
        if row is not None:
            return row["price"], row["title"]
    except (sqlite3.OperationalError, FileNotFoundError):
        pass

    url = f"https://krisha.kz/a/show/{listing_id}"
    with PoliteClient(delay_range=(0.5, 1.0)) as client:
        page = client.get(url)
    listing = parse_detail(page, url) if page else None
    if listing is None:
        raise RuntimeError("Не удалось загрузить объявление")
    try:
        upsert_listing({**listing, "source": "user"})
    except Exception:  # noqa: BLE001 — сохранение не должно ломать команду
        logger.warning("track: не удалось сохранить объявление в базу", exc_info=True)
    return listing.get("price"), listing.get("title")


def _handle_track_command(chat_id: int, text: str) -> None:
    """Команды /track [ссылка], /untrack <ссылка>|all."""
    from krisha.tracking import MAX_TRACKED_PER_CHAT, add_tracked, remove_tracked

    cmd, _, args = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    args = args.strip()
    match = KRISHA_URL_RE.search(args)

    if cmd == "/untrack":
        if not match and args.lower() not in ("all", "все", "всё"):
            tg_call("sendMessage", chat_id=chat_id, text=TRACK_HELP, parse_mode="HTML",
                    disable_web_page_preview=True)
            return
        listing_id = int(match.group(1)) if match else None
        removed = remove_tracked(chat_id, listing_id)
        tg_call("sendMessage", chat_id=chat_id,
                text="Убрал из слежки 👌" if removed else "Этого лота и не было в слежке 🙂")
        return

    if not match:
        text_out = TRACK_HELP + "\n\n" + _tracked_list_text(chat_id)
        tg_call("sendMessage", chat_id=chat_id, text=text_out, parse_mode="HTML",
                disable_web_page_preview=True)
        return

    listing_id = int(match.group(1))
    tg_call("sendChatAction", chat_id=chat_id, action="typing")
    try:
        price, title = _track_listing_meta(listing_id)
    except RuntimeError as exc:
        tg_call("sendMessage", chat_id=chat_id, text=f"Не получилось: {exc}")
        return

    ok, reason = add_tracked(chat_id, listing_id, price, title)
    if not ok and reason == "limit":
        tg_call("sendMessage", chat_id=chat_id,
                text=f"Лимит: не больше {MAX_TRACKED_PER_CHAT} лотов в слежке. "
                     "Убери что-нибудь: /untrack <ссылка>")
        return
    if not ok:
        tg_call("sendMessage", chat_id=chat_id, text="Уже слежу за этим лотом 👌")
        return

    name = html.escape(title or f"Объявление {listing_id}")
    price_txt = f" Текущая цена: <b>{fmt_tenge(price)}</b>." if price else ""
    tg_call("sendMessage", chat_id=chat_id, parse_mode="HTML",
            text=f"👀 Слежу за «{name}».{price_txt}\n"
                 "Пришлю алерт при изменении цены или снятии с продажи. "
                 "Список: /track, отписка: /untrack")


def public_base_url() -> str | None:
    """Публичный URL приложения: PUBLIC_BASE_URL или домен хостинга."""
    explicit = os.environ.get("PUBLIC_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    # Railway и Hugging Face Spaces выставляют свои домены автоматически
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("SPACE_HOST")
    return f"https://{domain}" if domain else None


def setup_webhook(retries: int = 3) -> bool:
    """Регистрирует webhook при старте приложения (no-op без токена/домена).

    С ретраями: на новом хостинге первый вызов может упасть по сети
    (DNS/egress ещё не готовы) — тогда бот молча оставался без webhook.
    """
    token = bot_token()
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN не задан — Telegram-бот выключен")
        return False
    base = public_base_url()
    if not base:
        logger.warning("Токен бота есть, но публичный URL неизвестен — webhook не настроен")
        return False
    for attempt in range(1, retries + 1):
        data = tg_call(
            "setWebhook",
            url=f"{base}{WEBHOOK_PATH}",
            secret_token=webhook_secret(token),
            allowed_updates=["message"],
            drop_pending_updates=True,
        )
        if data and data.get("ok"):
            logger.info("Telegram webhook настроен: %s%s", base, WEBHOOK_PATH)
            return True
        logger.warning("setWebhook попытка %d/%d не удалась: %s", attempt, retries, data)
        if attempt < retries:
            time.sleep(2 * attempt)
    return False


_WEBHOOK_CHECK_INTERVAL_S = 3600.0
_last_webhook_check: list[float] = [0.0]  # list — чтобы мутировать без global
_last_webhook_status: list[str] = ["unknown"]


def webhook_status(force: bool = False) -> str:
    """Статус webhook с самолечением (вызывается из /api/health).

    Не чаще раза в час спрашивает Telegram getWebhookInfo; если webhook
    слетел или смотрит не туда — перерегистрирует. Так пинг keepalive
    заодно чинит бота, даже если регистрация при старте не удалась.
    """
    token = bot_token()
    if not token:
        return "no_token"
    base = public_base_url()
    if not base:
        return "no_public_url"
    now = time.monotonic()
    if not force and now - _last_webhook_check[0] < _WEBHOOK_CHECK_INTERVAL_S:
        return _last_webhook_status[0]
    _last_webhook_check[0] = now
    data = tg_call("getWebhookInfo")
    if not data or not data.get("ok"):
        _last_webhook_status[0] = "unknown"
        return "unknown"
    current = (data.get("result") or {}).get("url") or ""
    expected = f"{base}{WEBHOOK_PATH}"
    if current == expected:
        _last_webhook_status[0] = "ok"
        return "ok"
    # Слетел или смотрит на старый хостинг — чиним
    fixed = setup_webhook(retries=1)
    _last_webhook_status[0] = "ok" if fixed else ("unset" if not current else "mismatch")
    return _last_webhook_status[0]
