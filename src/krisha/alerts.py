"""Ежедневные алерты: новые объявления с вердиктом «выгодно».

Запускается из GitHub Actions после рескрейпа (scripts/send_alerts.py):
берём объявления, впервые увиденные за последние ALERT_WINDOW_HOURS,
прогоняем через модель и рассылаем подписчикам те, что дешевле нижней
границы интервала справедливой цены (GOOD_DEAL) и проходят фильтры.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Any

from krisha.config import DB_PATH
from krisha.db import get_conn
from krisha.stats import DISTRICT_RU

logger = logging.getLogger(__name__)

ALERT_WINDOW_HOURS = 26  # запас к суточному крону
MAX_DEALS_PER_CHAT = 5

# issue #127 сделал sighting и докачку детали разными очередями: id попадает
# в базу сразу, а детальная страница — когда до неё дойдёт лимитированная
# очередь, порой через несколько проходов. Окно только по first_seen из-за
# этого пропускало лот навсегда: пока деталей нет, у строки нет ни area, ни
# нормальной цены (её отсекает `price > 0 AND area > 0`), а когда детали
# приезжают — first_seen уже вне окна. Поэтому смотрим ещё и на scraped_at,
# то есть на момент, когда лот стал пригоден для оценки.
#
# Плата за это — scraped_at обновляет и очередь дообновления устаревших
# деталей (issue #102), так что по одному только окну старый лот попадал бы
# в алерты повторно. От повторов защищает файл уже разосланных id.
ALERTED_PATH = Path(__file__).resolve().parents[2] / "data" / "alerted_ids.json"
ALERTED_KEEP = 2000


def load_alerted(path: Path | None = None) -> list[int]:
    import json

    path = path or ALERTED_PATH
    if not path.exists():
        return []
    try:
        return [int(x) for x in json.loads(path.read_text(encoding="utf-8"))]
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("alerts: битый %s — начинаем с нуля", path)
        return []


def remember_alerted(ids: list[int], path: Path | None = None) -> None:
    """Помечает лоты как разосланные, чтобы не слать их повторно."""
    from krisha.subscriptions import save_json_state

    path = path or ALERTED_PATH
    merged = (load_alerted(path) + [int(i) for i in ids])[-ALERTED_KEEP:]
    # encrypt=False: это id публичных объявлений, PII здесь нет
    save_json_state(path, merged, "state: разосланные в алертах лоты", encrypt=False)


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
    # issue #115: get_conn (WAL/busy_timeout) вместо голого sqlite3.connect —
    # рассылка идёт рядом по времени с рескрейпом, который активно пишет в базу.
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM listings WHERE is_active = 1 AND price > 0 AND area > 0 "
            "AND (first_seen >= datetime('now', ?) OR scraped_at >= datetime('now', ?))",
            (f"-{hours} hours", f"-{hours} hours"),
        ).fetchall()
    already = set(load_alerted())
    return [dict(r) for r in rows if r["id"] not in already]


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


def send_alerts(
    subscriptions: dict[str, dict], deals: list[dict], persist: bool = True
) -> int:
    """Рассылает алерты; возвращает число отправленных сообщений."""
    from krisha.bot import tg_call

    sent = 0
    delivered_ids: set[int] = set()
    for chat_id, flt in subscriptions.items():
        matched = [d for d in deals if match_filters(d, flt)][:MAX_DEALS_PER_CHAT]
        if not matched:
            continue
        resp = tg_call("sendMessage", chat_id=int(chat_id), text=format_alert(matched),
                       parse_mode="HTML", disable_web_page_preview=True)
        if resp and resp.get("ok"):
            sent += 1
            delivered_ids.update(int(d["id"]) for d in matched if d.get("id") is not None)
    # Помечаем только реально доставленные: окно теперь ловит лот и по
    # scraped_at, так что без этой отметки очередь дообновления деталей
    # (issue #102) присылала бы один и тот же лот снова и снова.
    if persist and delivered_ids:
        remember_alerted(sorted(delivered_ids))
    return sent
