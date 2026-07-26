"""Этап 4 роадмапа: сигналы рынка для карточки оценки.

- история цены объявления (точки из price_history + текущая длительность);
- оценка ликвидности: медианные «дни на рынке» похожих (район + комнаты)
  уже снятых объявлений. Данных мало → None, блок на фронте не показывается.
"""

import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krisha.config import DB_PATH, MAX_TRUSTED_DELIST_LAG_DAYS
from krisha.db import get_price_history, use_conn

MIN_DELISTED_SAMPLE = 15  # меньше снятых аналогов → оценку не показываем
MIN_BAND_SAMPLE = 20      # ценовые полосы — только на приличной выборке
MIN_CITY_SAMPLE = 30      # фолбэк на весь город требует больше данных
NEAR_BAND_PCT = 5.0       # ±5% от медианного ₸/м² сегмента = «в рынке»

# Снятые объявления для оценки срока продажи. Честные фильтры:
# - is_active = 0 и delisted_at заполнен — только реально помеченные снятыми;
# - days >= 1 — нулевые/отрицательные длительности это шум (дубли, ошибки);
# - first_seen_cohort IS NULL — только органика. Метку ставит на строку сам
#   проход (см. rescrape.sweep): 'initial' — когорта первого сбора, 'gap:*' —
#   лоты, впервые увиденные после провала. У обеих first_seen — дата, когда
#   МЫ их заметили, а не дата публикации, поэтому «срок» у них фикция. Раньше
#   здесь стояла эвристика first_seen >= MIN(first_seen) + 2: она ловила
#   только когорту первого сбора и ничего не знала о провалах сбора.
#
# Длительность считаем last_seen - first_seen, а НЕ delisted_at - first_seen.
# delisted_at — момент, когда мы ЗАМЕТИЛИ пропажу, а не когда лот ушёл с
# рынка: между last_seen и delisted_at лот уже не наблюдался. Это не мелочь —
# на проде систематические +4 дня к каждому эпизоду, медиана «срока продажи»
# падает с 6.0 до 3.1 дня. Истина лежит между двумя оценками (лот был жив в
# last_seen и мёртв в delisted_at), но нижняя граница ошибается на интервал
# наблюдения (~1 день), а верхняя — на весь лаг детекции (~4 дня).
_DELISTED_SQL = (
    "SELECT julianday(last_seen) - julianday(first_seen) AS days, price, area "
    "FROM listings "
    "WHERE is_active = 0 AND delisted_at IS NOT NULL AND first_seen IS NOT NULL "
    "AND last_seen IS NOT NULL "
    "AND julianday(last_seen) - julianday(first_seen) >= 1 "
    # Цензурированные эпизоды: лот не наблюдался дольше порога, момент ухода
    # с рынка неизвестен. Фильтр самоописываемый — отсекает когорту после
    # ЛЮБОГО провала сбора, не заглядывая в data_gaps и не зная никаких дат.
    # После слепоты 14–26.07.2026 лаг у всей волны будет ~14 дней, и она
    # отсеется сама. Порог общий с tracking.py (см. config): один и тот же
    # вопрос «доверяем ли мы этому снятию» не может иметь двух ответов.
    f"AND julianday(delisted_at) - julianday(last_seen) <= {MAX_TRUSTED_DELIST_LAG_DAYS} "
    # Строго IS NULL, а не «<> 'initial'»: в SQL NULL <> 'initial' даёт NULL,
    # то есть вся органика (cohort IS NULL) молча отфильтровалась бы и блок
    # «срок продажи» исчез бы из каждой карточки.
    "AND first_seen_cohort IS NULL"
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc)


def price_history_points(
    listing_id: int | None, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    """Точки истории цены для графика. Объявления нет в базе → []."""
    if listing_id is None:
        return []
    try:
        return get_price_history(int(listing_id), conn=conn)
    except (sqlite3.OperationalError, FileNotFoundError):
        return []


def days_on_market(
    listing_id: int | None, conn: sqlite3.Connection | None = None
) -> int | None:
    """Сколько дней объявление в выдаче (first_seen → last_seen/now)."""
    if listing_id is None:
        return None
    with use_conn(conn, DB_PATH) as conn:
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


def _delisted_rows(
    conn: sqlite3.Connection, district: str | None = None, rooms: int | None = None
) -> list[sqlite3.Row]:
    """Снятые объявления сегмента (или всего города) с длительностью и ₸/м²."""
    sql, params = _DELISTED_SQL, []
    if district is not None and rooms is not None:
        sql += " AND district = ? AND rooms = ?"
        params = [district, int(rooms)]
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _price_band(deviation_pct: float) -> str:
    """Полоса цены по отклонению от медианы: below / near / above."""
    if deviation_pct <= -NEAR_BAND_PCT:
        return "below"
    if deviation_pct < NEAR_BAND_PCT:
        return "near"
    return "above"


def _band_stats(rows: list[sqlite3.Row], band: str) -> tuple[int, int] | None:
    """Медиана дней в ценовой полосе сегмента (по отклонению ₸/м² от медианы)."""
    priced = [r for r in rows if r["price"] and r["area"]]
    if len(priced) < MIN_BAND_SAMPLE:
        return None
    median_ppsm = statistics.median(r["price"] / r["area"] for r in priced)
    days = [
        r["days"]
        for r in priced
        if _price_band((r["price"] / r["area"] / median_ppsm - 1) * 100) == band
    ]
    if len(days) < MIN_BAND_SAMPLE:
        return None
    return round(statistics.median(days)), len(days)


def liquidity_estimate(
    district: str | None,
    rooms: int | None,
    diff_pct: float | None = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """«Срок продажи» v1: медианные дни до снятия у похожих объявлений.

    Уровни (сколько данных хватило, столько и честности):
    - сегмент район + комнаты (>= MIN_DELISTED_SAMPLE снятых);
    - фолбэк на весь город (>= MIN_CITY_SAMPLE), scope = "city";
    - если известно отклонение цены от справедливой (diff_pct) и в ценовой
      полосе сегмента >= MIN_BAND_SAMPLE снятых — добавляем band_median_days:
      «похожие по цене уходят за ~N дней».

    Важно: снятие != продажа (объявление могли просто удалить), поэтому всегда
    возвращаем размер выборки. Мало данных → None, блок не показывается;
    история копится ежедневным рескрейпом (scripts/rescrape.py).
    """
    if not district or rooms is None:
        return None
    with use_conn(conn, db_path) as conn:
        rows = _delisted_rows(conn, district, rooms)
        result: dict[str, Any] | None = None
        if len(rows) >= MIN_DELISTED_SAMPLE:
            result = {
                "median_days": round(statistics.median(r["days"] for r in rows)),
                "sample": len(rows),
                "scope": "district_rooms",
            }
        else:
            city = _delisted_rows(conn)
            if len(city) >= MIN_CITY_SAMPLE:
                result = {
                    "median_days": round(statistics.median(r["days"] for r in city)),
                    "sample": len(city),
                    "scope": "city",
                }
    if result is None:
        return None
    result.update({"band": None, "band_median_days": None, "band_sample": None})
    if diff_pct is not None and result["scope"] == "district_rooms":
        band = _price_band(float(diff_pct))
        stats = _band_stats(rows, band)
        if stats is not None:
            result.update(
                {"band": band, "band_median_days": stats[0], "band_sample": stats[1]}
            )
    return result
