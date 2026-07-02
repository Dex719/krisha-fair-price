"""Ежедневные алерты: новые объявления с вердиктом «выгодно».

Запускается из GitHub Actions после рескрейпа (scripts/send_alerts.py):
берём объявления, впервые увиденные за последние ALERT_WINDOW_HOURS,
прогоняем через модель и рассылаем подписчикам те, что дешевле нижней
границы интервала справедливой цены (GOOD_DEAL) и проходят фильтры.
"""

from __future__ import annotations

import html
import logging
import sqlite3
from pathlib import Path
from typing import Any

from krisha.config import DB_PATH
from krisha.stats import DISTRICT_RU

logger = logging.getLogger(__name__)

ALERT_WINDOW_HOURS = 26  # запас к суточному крону
MAX_DEALS_PER_CHAT = 5


def match_filters(listing: dict[str, Any], flt: dict[str, Any]) -> bool:
    if flt.get("rooms") and listing.get("rooms") != flt["rooms"]:
        return False
    if flt.get("max_price") and (listing.get("price") or 0) > flt["max_price"]:
        return False
    if flt.get("district") and listing.get("district") != flt["district"]:
        return False
    return True


def new_listings(db_path: Path | str = DB_PATH, hours: int = ALERT_WINDOW_HOURS) -> list[dict]:
    """Активные объявления, впервые увиденные за последние `hours` часов."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM listings WHERE is_active = 1 AND price > 0 AND area > 0 "
            "AND first_seen >= datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchall()
    return [dict(r) for r in rows]


def find_good_deals(db_path: Path | str = DB_PATH, hours: int = ALERT_WINDOW_HOURS) -> list[dict]:
    """Новые объявления с вердиктом GOOD_DEAL, отсортированы по выгоде."""
    from krisha.predict import predict_from_listing

    deals = []
    for listing in new_listings(db_path, hours):
        try:
            result = predict_from_listing(listing, flags_live=False)
        except Exception as exc:  # noqa: BLE001 — одна кривая строка не должна ронять рассылку
            logger.warning("alerts: predict для %s не удался: %s", listing.get("id"), exc)
            continue
        if result.get("verdict") != "GOOD_DEAL":
            continue
        deals.append({**listing, "fair_price": result["fair_price"],
                      "diff_pct": result.get("diff_pct")})
    deals.sort(key=lambda d: d.get("diff_pct") or 0)  # самая большая скидка первой
    return deals


def format_alert(deals: list[dict]) -> str:
    """HTML-сообщение для Telegram со списком выгодных лотов."""
    lines = ["🔥 <b>Новые выгодные объявления</b>", ""]
    for d in deals:
        price_mln = d["price"] / 1_000_000
        fair_mln = d["fair_price"] / 1_000_000
        district = DISTRICT_RU.get(d.get("district") or "", "")
        title = html.escape(d.get("title") or f"{d.get('rooms', '?')}-комн, {d.get('area', '?')} м²")
        lines.append(f"🏠 <a href=\"{html.escape(d['url'])}\">{title}</a>")
        detail = f"💰 {price_mln:.1f} млн ₸ (оценка {fair_mln:.1f} млн, {d['diff_pct']:+.1f}%)"
        if district:
            detail += f" · {district}"
        lines.append(detail)
        lines.append("")
    lines.append("Отписаться: /alerts_off")
    return "\n".join(lines)


def send_alerts(subscriptions: dict[str, dict], deals: list[dict]) -> int:
    """Рассылает алерты; возвращает число отправленных сообщений."""
    from krisha.bot import tg_call

    sent = 0
    for chat_id, flt in subscriptions.items():
        matched = [d for d in deals if match_filters(d, flt)][:MAX_DEALS_PER_CHAT]
        if not matched:
            continue
        resp = tg_call("sendMessage", chat_id=int(chat_id), text=format_alert(matched),
                       parse_mode="HTML", disable_web_page_preview=True)
        if resp and resp.get("ok"):
            sent += 1
    return sent
