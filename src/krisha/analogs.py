"""Аналоги: «похожие квартиры» рядом с оценкой (задача 3 бэклога).

kNN по ключевым фичам среди активных объявлений базы: кандидатов берём
SQL-фильтром (те же комнаты, площадь ±25%), затем ранжируем взвешенным
расстоянием по площади, геопозиции и году постройки. Никаких внешних
индексов — базы в ~40к строк SQLite обходит мгновенно.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from typing import Any

from krisha.config import DB_PATH
from krisha.db import use_conn

logger = logging.getLogger(__name__)

MAX_ANALOGS = 5
AREA_TOLERANCE = 0.25  # кандидаты: площадь ±25%

# Веса расстояния: нормируем каждую компоненту на «типичный масштаб»
_AREA_SCALE = 15.0  # м²
_GEO_SCALE_KM = 3.0  # км
_YEAR_SCALE = 12.0  # лет
_MISSING_PENALTY = 1.5  # у кандидата нет координат/года — считаем далёким


def _geo_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Приближённое расстояние в км (equirectangular — для города достаточно)."""
    kx = 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    ky = 111.32
    return math.hypot((lon1 - lon2) * kx, (lat1 - lat2) * ky)


def _distance(subject: dict[str, Any], cand: sqlite3.Row) -> float:
    d = 0.0
    area_s, area_c = subject.get("area"), cand["area"]
    if area_s and area_c:
        d += (abs(area_s - area_c) / _AREA_SCALE) ** 2
    lat_s, lon_s = subject.get("lat"), subject.get("lon")
    if lat_s and lon_s and cand["lat"] and cand["lon"]:
        d += (_geo_km(lat_s, lon_s, cand["lat"], cand["lon"]) / _GEO_SCALE_KM) ** 2
    else:
        d += _MISSING_PENALTY**2
    year_s, year_c = subject.get("year_built"), cand["year_built"]
    if year_s and year_c:
        d += (abs(year_s - year_c) / _YEAR_SCALE) ** 2
    elif year_s:
        # Кандидат без года постройки раньше не получал НИЧЕГО за это поле, то
        # есть был «идеально похож» по возрасту дома и обгонял кандидатов с
        # известным, но не совпадающим годом. Штрафуем — как для координат.
        # Если года нет у самого subject, слагаемое одинаково для всех
        # кандидатов и на порядок не влияет, поэтому его не добавляем.
        d += _MISSING_PENALTY**2
    return math.sqrt(d)


def find_analogs(
    subject: dict[str, Any],
    db_path=DB_PATH,
    k: int = MAX_ANALOGS,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Топ-k активных объявлений, похожих на subject. Ошибки → [] (fail-soft).

    issue #110/#115: `get_conn`/`use_conn` вместо голого `sqlite3.connect` —
    получает WAL/busy_timeout (не гоняется мимо них на самом горячем пути,
    вызывается на каждый /api/predict) и переиспользует соединение,
    открытое на весь запрос, если оно передано вызывающим кодом.
    """
    rooms, area = subject.get("rooms"), subject.get("area")
    if not rooms or not area:
        return []
    try:
        with use_conn(conn, db_path) as c:
            rows = c.execute(
                "SELECT id, url, title, price, area, rooms, floor, total_floors, "
                "year_built, district, lat, lon FROM listings "
                "WHERE is_active = 1 AND price > 0 AND area > 0 AND rooms = ? "
                "AND area BETWEEN ? AND ? AND id != ?",
                (
                    int(rooms),
                    float(area) * (1 - AREA_TOLERANCE),
                    float(area) * (1 + AREA_TOLERANCE),
                    int(subject.get("id") or 0),
                ),
            ).fetchall()
    except (sqlite3.OperationalError, FileNotFoundError) as exc:
        logger.warning("analogs: база недоступна: %s", exc)
        return []

    # Тот же район в приоритете: сначала соседи по району, потом остальные
    district = subject.get("district")
    ranked = sorted(
        rows,
        key=lambda r: (
            0 if district and r["district"] == district else 1,
            _distance(subject, r),
        ),
    )
    out = []
    for r in ranked[:k]:
        out.append(
            {
                "id": r["id"],
                "url": r["url"] or f"https://krisha.kz/a/show/{r['id']}",
                "title": r["title"],
                "price": r["price"],
                "area": r["area"],
                "rooms": r["rooms"],
                "floor": r["floor"],
                "total_floors": r["total_floors"],
                "year_built": r["year_built"],
                "district": r["district"],
                "ppsm": round(r["price"] / r["area"]) if r["area"] else None,
            }
        )
    return out
