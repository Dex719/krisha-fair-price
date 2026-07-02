"""Ежемесячный отчёт о рынке в Telegram (задача 8 бэклога).

Собирается из той же статистики, что /stats (`compute_stats`): объём рынка,
медианы, динамика ₸/м² за месяц из недельного тренда, топ районов.
Отправляется 1-го числа (проверка в scripts/send_alerts.py после ночного
рескрейпа) владельцу (`TG_ADMIN_CHAT_ID`) и, если задан, в канал
(`TG_CHANNEL_ID`).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from krisha.monitoring import ADMIN_CHAT_ENV
from krisha.stats import compute_stats

logger = logging.getLogger(__name__)

CHANNEL_ENV = "TG_CHANNEL_ID"

_MONTHS_RU = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


def _month_delta_pct(trend: list[dict]) -> float | None:
    """Изменение медианы ₸/м² за ~месяц: последняя неделя vs 4 недели назад."""
    if len(trend) < 5:
        return None
    old, new = trend[-5]["median_ppsm"], trend[-1]["median_ppsm"]
    if not old:
        return None
    return (new / old - 1) * 100


def build_monthly_report(stats: dict | None = None) -> str:
    """HTML-отчёт для Telegram по текущему состоянию базы."""
    stats = stats or compute_stats()
    now = datetime.now(timezone.utc)
    title = f"{_MONTHS_RU[now.month - 1]} {now.year}".capitalize()

    lines = [
        f"📊 <b>Рынок квартир Алматы — {title}</b>",
        "",
        f"Объявлений в базе: <b>{stats['total_listings']:,}</b>".replace(",", " "),
        f"Медианная цена: <b>{stats['median_price'] / 1e6:.1f} млн ₸</b>",
        f"Медиана за м²: <b>{stats['median_ppsm'] / 1e3:.0f} тыс ₸</b>",
    ]

    delta = _month_delta_pct(stats.get("trend") or [])
    if delta is not None:
        arrow = "📈" if delta > 0.2 else "📉" if delta < -0.2 else "➡️"
        lines.append(f"Динамика ₸/м² за месяц: {arrow} <b>{delta:+.1f}%</b>")

    districts = stats.get("by_district") or []
    if districts:
        lines += ["", "<b>₸/м² по районам:</b>"]
        for d in districts:
            lines.append(
                f"• {d['district']}: {d['median_ppsm'] / 1e3:.0f} тыс ₸ ({d['n']} лотов)"
            )

    rooms = stats.get("by_rooms") or []
    if rooms:
        lines += ["", "<b>Медианная цена по комнатности:</b>"]
        for r in rooms:
            lines.append(f"• {r['rooms']}-комн: {r['median_price'] / 1e6:.1f} млн ₸")

    lines += ["", "🤖 Данные: живая база krisha-fair-price"]
    return "\n".join(lines)


def send_monthly_report(dry_run: bool = False) -> int:
    """Шлёт отчёт админу и в канал (если заданы). Возвращает число отправок."""
    text = build_monthly_report()
    if dry_run:
        print(text)
        return 0

    from krisha.bot import tg_call

    sent = 0
    for env in (ADMIN_CHAT_ENV, CHANNEL_ENV):
        chat = os.environ.get(env)
        if not chat:
            continue
        resp = tg_call("sendMessage", chat_id=chat, text=text, parse_mode="HTML")
        if resp and resp.get("ok"):
            sent += 1
        else:
            logger.warning("report: не отправилось в %s: %s", env, resp)
    if not sent:
        logger.info("report: ни один чат не задан — отчёт не отправлен")
    return sent
