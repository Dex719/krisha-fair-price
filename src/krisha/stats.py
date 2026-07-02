"""Статистика рынка: общее число объявлений, ₸/м² по районам, распределение цен.

Используется в /api/stats. На деплое без БД статистика читается из
models/stats.json — снапшота, который создаётся при обучении (scripts/train.py).
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from krisha.config import DB_PATH, MODELS_DIR

logger = logging.getLogger(__name__)

STATS_SNAPSHOT_PATH = MODELS_DIR / "stats.json"

# Транслит krisha.kz → человеческое название района
DISTRICT_RU = {
    "Almalinskiy_r-n": "Алмалинский",
    "Alatauskiy_r-n": "Алатауский",
    "Auezovskiy_r-n": "Ауэзовский",
    "Bostandykskiy_r-n": "Бостандыкский",
    "Zhetysuskiy_r-n": "Жетысуский",
    "Medeuskiy_r-n": "Медеуский",
    "Nauryzbayskiy_r-n": "Наурызбайский",
    "Turksibskiy_r-n": "Турксибский",
}

# Границы корзин гистограммы цен (₸)
PRICE_BINS = [0, 20, 30, 40, 50, 60, 80, 100, 150, 250, 10_000]  # млн ₸


def _weekly_trend(db_path: Path | str, max_weeks: int = 12, min_n: int = 100) -> list[dict]:
    """Медиана ₸/м² по неделям: активные в ту неделю объявления с ценой,
    актуальной на конец недели (реконструкция из price_history)."""
    with sqlite3.connect(db_path) as conn:
        listings = pd.read_sql(
            "SELECT id, area, first_seen, last_seen FROM listings "
            "WHERE area > 0 AND price > 0 AND first_seen IS NOT NULL",
            conn,
        )
        ph = pd.read_sql(
            "SELECT listing_id, price, observed_at FROM price_history WHERE price > 0",
            conn,
        )
    if listings.empty or ph.empty:
        return []

    listings["first_seen"] = pd.to_datetime(listings["first_seen"], format="mixed")
    listings["last_seen"] = pd.to_datetime(listings["last_seen"], format="mixed").fillna(
        listings["first_seen"]
    )
    ph["observed_at"] = pd.to_datetime(ph["observed_at"], format="mixed")
    ph = ph.sort_values("observed_at")

    end = pd.Timestamp.utcnow().tz_localize(None)
    week_end = (end - pd.offsets.Week(weekday=6)).normalize() + pd.Timedelta(days=1)
    trend = []
    for i in range(max_weeks - 1, -1, -1):
        w_end = week_end - pd.Timedelta(weeks=i)
        w_start = w_end - pd.Timedelta(weeks=1)
        if i == 0:
            w_end = end  # текущая (неполная) неделя — до «сейчас»
        active = listings[(listings["first_seen"] < w_end) & (listings["last_seen"] >= w_start)]
        if len(active) < min_n:
            continue
        # цена на момент w_end: последнее наблюдение не позже конца недели
        seen = ph[ph["observed_at"] < w_end].groupby("listing_id")["price"].last()
        grp = active.join(seen.rename("price"), on="id").dropna(subset=["price"])
        if len(grp) < min_n:
            continue
        trend.append({
            "week": w_start.strftime("%d.%m"),
            "median_ppsm": int((grp["price"] / grp["area"]).median()),
            "n": int(len(grp)),
        })
    return trend


def compute_stats(db_path: Path | str = DB_PATH) -> dict:
    """Считает статистику по базе. Бросает FileNotFoundError, если БД нет."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"БД не найдена: {db_path}")

    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql(
            "SELECT price, area, rooms, district, category FROM listings "
            "WHERE price IS NOT NULL AND area IS NOT NULL AND area > 0",
            conn,
        )

    df["ppsm"] = df["price"] / df["area"]
    df["price_mln"] = df["price"] / 1_000_000

    by_district = []
    for key, grp in df.dropna(subset=["district"]).groupby("district"):
        by_district.append({
            "district": DISTRICT_RU.get(key, key),
            "n": int(len(grp)),
            "median_ppsm": int(grp["ppsm"].median()),
            "median_price": int(grp["price"].median()),
        })
    by_district.sort(key=lambda x: x["median_ppsm"], reverse=True)

    hist = pd.cut(df["price_mln"], bins=PRICE_BINS, right=False).value_counts().sort_index()
    price_hist = [
        {
            "label": f"{int(iv.left)}–{int(iv.right)} млн" if iv.right < 10_000 else f"{int(iv.left)}+ млн",
            "count": int(cnt),
        }
        for iv, cnt in hist.items()
    ]

    by_rooms = [
        {"rooms": int(r), "n": int(len(g)), "median_price": int(g["price"].median())}
        for r, g in df.dropna(subset=["rooms"]).groupby("rooms")
        if 1 <= r <= 6
    ]

    cat = df["category"].fillna("unknown").value_counts().to_dict()

    trend = _weekly_trend(db_path)

    return {
        "total_listings": int(len(df)),
        "median_price": int(df["price"].median()),
        "median_ppsm": int(df["ppsm"].median()),
        "by_district": by_district,
        "price_hist": price_hist,
        "by_rooms": by_rooms,
        "trend": trend,
        "by_category": {
            # на krisha.kz вторичка идёт как "kvartiry", считаем всё не-новостройки
            "novostroiki": int(cat.get("novostroiki", 0)),
            "vtorichka": int(len(df) - cat.get("novostroiki", 0)),
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "db",
    }


def snapshot_stats(db_path: Path | str = DB_PATH) -> dict:
    """Считает статистику и сохраняет в models/stats.json (для деплоя без БД)."""
    stats = compute_stats(db_path)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    STATS_SNAPSHOT_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    logger.info("Снапшот статистики: %s", STATS_SNAPSHOT_PATH)
    return stats


def heatmap_points(
    db_path: Path | str = DB_PATH, cell_deg: float = 0.004, min_n: int = 2
) -> list[dict]:
    """Сетка для тепловой карты ₸/м²: активные лоты с координатами,
    сгруппированные в ячейки ~400 м. Возвращает [{lat, lon, ppsm, n}]."""
    if not Path(db_path).exists():
        raise FileNotFoundError(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ROUND(lat / :cell) * :cell AS clat,
                   ROUND(lon / :cell) * :cell AS clon,
                   AVG(price / area) AS ppsm,
                   COUNT(*) AS n
            FROM listings
            WHERE is_active = 1 AND price > 0 AND area > 0
              AND lat IS NOT NULL AND lon IS NOT NULL
            GROUP BY clat, clon
            HAVING COUNT(*) >= :min_n
            """,
            {"cell": cell_deg, "min_n": min_n},
        ).fetchall()
    return [
        {"lat": round(clat, 5), "lon": round(clon, 5), "ppsm": round(ppsm), "n": n}
        for clat, clon, ppsm, n in rows
    ]


def get_stats() -> dict:
    """Живая статистика из БД, иначе снапшот. Бросает FileNotFoundError, если нет ничего."""
    try:
        return compute_stats()
    except FileNotFoundError:
        pass
    if STATS_SNAPSHOT_PATH.exists():
        stats = json.loads(STATS_SNAPSHOT_PATH.read_text())
        stats["source"] = "snapshot"
        return stats
    raise FileNotFoundError("Нет ни БД, ни снапшота статистики — запусти crawl + train")
