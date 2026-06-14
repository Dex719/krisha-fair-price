"""Логирование событий и агрегация для приватной dev-панели аналитики.

Каждая оценка (сайт `/api/predict` и Telegram-бот) пишет строку в таблицу
`events`. Панель `/admin` показывает по этим данным: посещаемость по дням,
уникальные посетители, что искали, что выдавали, разбивку по источникам и т.д.

Приватность: вместо IP/chat_id храним соль+hash (`visitor`), без персональных
данных. Логирование никогда не должно ронять основной запрос — все вызовы
`log_event` обёрнуты в try/except у вызывающей стороны не требуется, ошибки
глотаются здесь.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from krisha.config import DB_PATH
from krisha.db import get_conn

logger = logging.getLogger(__name__)

# Соль для хэша посетителя — чтобы по hash нельзя было восстановить IP.
# Можно задать через env ANALYTICS_SALT (рекомендуется в проде).
_SALT = os.environ.get("ANALYTICS_SALT", "krisha-fair-price-analytics-v1")

EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
    source        TEXT,        -- web / bot
    visitor       TEXT,        -- соль+hash от IP (web) или chat_id (bot)
    listing_id    INTEGER,
    url           TEXT,
    district      TEXT,
    rooms         INTEGER,
    area          REAL,
    actual_price  INTEGER,     -- цена объявления (₸)
    fair_price    INTEGER,     -- оценка модели (₸)
    verdict       TEXT,        -- GOOD_DEAL / FAIR / OVERPRICED
    diff_pct      REAL,        -- отклонение цены от оценки, %
    response_ms   INTEGER,     -- время ответа
    status        TEXT         -- ok / error
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_visitor ON events(visitor);
"""


def init_events(db_path: Path | str = DB_PATH) -> None:
    """Создаёт таблицу events (идемпотентно)."""
    with get_conn(db_path) as conn:
        conn.executescript(EVENTS_SCHEMA)


def visitor_hash(raw: str | int | None) -> str:
    """Анонимный устойчивый id посетителя: соль + sha1, первые 12 символов."""
    if raw is None:
        raw = "?"
    return hashlib.sha1(f"{_SALT}|{raw}".encode()).hexdigest()[:12]


def log_event(
    *,
    source: str,
    visitor_raw: str | int | None,
    result: dict[str, Any] | None = None,
    response_ms: int | None = None,
    status: str = "ok",
    url: str | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    """Пишет одно событие оценки. Любая ошибка глотается — не ломаем запрос."""
    try:
        r = result or {}
        district = None
        for item in r.get("details", []) or []:
            if isinstance(item, dict) and item.get("label") == "Район":
                district = item.get("value")
                break
        with get_conn(db_path) as conn:
            try:
                _insert(conn, source, visitor_raw, r, response_ms, status, url, district)
            except sqlite3.OperationalError:
                conn.executescript(EVENTS_SCHEMA)
                _insert(conn, source, visitor_raw, r, response_ms, status, url, district)
    except Exception:  # аналитика не должна влиять на основной ответ
        logger.exception("Не удалось записать событие аналитики")


def _insert(conn, source, visitor_raw, r, response_ms, status, url, district) -> None:
    conn.execute(
        """INSERT INTO events
           (source, visitor, listing_id, url, district, rooms, area,
            actual_price, fair_price, verdict, diff_pct, response_ms, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            source,
            visitor_hash(visitor_raw),
            r.get("listing_id"),
            url or r.get("url"),
            district or r.get("district"),
            r.get("rooms"),
            r.get("area"),
            r.get("actual_price"),
            int(r["fair_price"]) if r.get("fair_price") is not None else None,
            r.get("verdict"),
            r.get("diff_pct"),
            response_ms,
            status,
        ),
    )


# --- Агрегация для панели ------------------------------------------------


def get_admin_stats(days: int = 30, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    """Сводная статистика за последние `days` дней (по локальному времени БД)."""
    days = max(1, min(int(days), 365))
    since = f"-{days} days"
    with get_conn(db_path) as conn:
        try:
            return _aggregate(conn, since, days)
        except sqlite3.OperationalError:
            conn.executescript(EVENTS_SCHEMA)
            return _aggregate(conn, since, days)


def _scalar(conn, sql, params=()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _aggregate(conn, since: str, days: int) -> dict[str, Any]:
    where = f"WHERE ts >= datetime('now','{since}')"

    total_requests = _scalar(conn, f"SELECT COUNT(*) FROM events {where}")
    unique_visitors = _scalar(conn, f"SELECT COUNT(DISTINCT visitor) FROM events {where}")
    today = "WHERE date(ts) = date('now')"
    requests_today = _scalar(conn, f"SELECT COUNT(*) FROM events {today}")
    visitors_today = _scalar(conn, f"SELECT COUNT(DISTINCT visitor) FROM events {today}")
    errors = _scalar(conn, f"SELECT COUNT(*) FROM events {where} AND status != 'ok'")
    avg_ms = _scalar(
        conn, f"SELECT AVG(response_ms) FROM events {where} AND response_ms IS NOT NULL"
    )

    # Заходы по дням (заполняем нулями пропущенные дни)
    rows = conn.execute(
        f"""SELECT date(ts) d, COUNT(*) reqs, COUNT(DISTINCT visitor) vis
            FROM events {where} GROUP BY date(ts)"""
    ).fetchall()
    by_day_map = {r["d"]: {"requests": r["reqs"], "visitors": r["vis"]} for r in rows}
    by_day = []
    now = time.time()
    for i in range(days - 1, -1, -1):
        d = time.strftime("%Y-%m-%d", time.gmtime(now - i * 86400))
        e = by_day_map.get(d, {"requests": 0, "visitors": 0})
        by_day.append({"day": d, "requests": e["requests"], "visitors": e["visitors"]})

    by_source = {
        r["source"] or "?": r["n"]
        for r in conn.execute(
            f"SELECT source, COUNT(*) n FROM events {where} GROUP BY source"
        )
    }
    by_verdict = {
        r["verdict"] or "—": r["n"]
        for r in conn.execute(
            f"SELECT verdict, COUNT(*) n FROM events {where} "
            f"AND status='ok' GROUP BY verdict"
        )
    }
    by_district = [
        {"district": r["district"], "n": r["n"]}
        for r in conn.execute(
            f"""SELECT district, COUNT(*) n FROM events {where}
                AND district IS NOT NULL AND district != ''
                GROUP BY district ORDER BY n DESC LIMIT 12"""
        )
    ]
    by_rooms = [
        {"rooms": r["rooms"], "n": r["n"]}
        for r in conn.execute(
            f"""SELECT rooms, COUNT(*) n FROM events {where}
                AND rooms IS NOT NULL GROUP BY rooms ORDER BY rooms"""
        )
    ]
    by_hour = [0] * 24
    for r in conn.execute(
        f"SELECT CAST(strftime('%H', ts) AS INT) h, COUNT(*) n FROM events {where} GROUP BY h"
    ):
        if r["h"] is not None:
            by_hour[int(r["h"])] = r["n"]

    return {
        "days": days,
        "total_requests": total_requests,
        "unique_visitors": unique_visitors,
        "requests_today": requests_today,
        "visitors_today": visitors_today,
        "errors": errors,
        "avg_response_ms": avg_ms,
        "by_day": by_day,
        "by_source": by_source,
        "by_verdict": by_verdict,
        "by_district": by_district,
        "by_rooms": by_rooms,
        "by_hour": by_hour,
    }


def recent_events(limit: int = 50, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """Последние события для таблицы в панели."""
    limit = max(1, min(int(limit), 500))
    with get_conn(db_path) as conn:
        try:
            rows = conn.execute(
                """SELECT ts, source, visitor, listing_id, url, district, rooms,
                          actual_price, fair_price, verdict, diff_pct, status
                   FROM events ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]
