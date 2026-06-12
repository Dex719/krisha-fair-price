"""Этап 4 роадмапа: сигналы рынка для карточки оценки.

- история цены объявления (точки из price_history + текущая длительность);
- оценка ликвидности: медианные «дни на рынке» похожих (район + комнаты)
  уже снятых объявлений. Данных мало → None, блок на фронте не показывается.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Any

from krisha.config import DB_PATH
from krisha.db import get_conn, get_price_history

MIN_DELISTED_SAMPLE = 15  # меньше снятых аналогов → оценку не показываем


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc)


def price_history_points(listing_id: int | None) -> list[dict[str, Any]]:
    """Точки истории цены для графика. Объявления нет в базе → []."""
    if listing_id is None:
        return []
    try:
        return get_price_history(int(listing_id))
    except (sqlite3.OperationalError, FileNotFoundError):
        return []


def days_on_market(listing_id: int | None) -> int | None:
    """Сколько дней объявление в выдаче (first_seen → last_seen/now)."""
    if listing_id is None:
        return None
    with get_conn(DB_PATH) as conn:
        try:
            row = conn.execute(
                "SELECT first_seen, is_active, delisted_at, last_seen "
                "FROM listings WHERE id = ?",
                (int(listing_id),),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    if row is None:
        return None
    start = _parse_dt(row["first_seen"])
    end = (
        _parse_dt(row["delisted_at"] or row["last_seen"])
        if not row["is_active"]
        else datetime.now(timezone.utc)
    )
    if start is None or end is None:
        return None
    return max(0, (end - start).days)


def liquidity_estimate(district: str | None, rooms: int | None) -> dict[str, Any] | None:
    """Медианные дни на рынке снятых аналогов (район + комнаты).

    Возвращает {"median_days": ..., "sample": ...} или None, если данных мало —
    история копится регулярным рескрейпом (scripts/rescrape.py).
    """
    if not district or rooms is None:
        return None
    with get_conn(DB_PATH) as conn:
        try:
            rows = conn.execute(
                "SELECT julianday(COALESCE(delisted_at, last_seen)) - julianday(first_seen) "
                "FROM listings WHERE is_active = 0 AND district = ? AND rooms = ? "
                "AND first_seen IS NOT NULL",
                (district, int(rooms)),
            ).fetchall()
        except sqlite3.OperationalError:
            return None
    days = sorted(max(0.0, r[0]) for r in rows if r[0] is not None)
    if len(days) < MIN_DELISTED_SAMPLE:
        return None
    return {"median_days": round(days[len(days) // 2]), "sample": len(days)}
