"""Прогноз цен на 3–6 месяцев по районам (задача 12 бэклога).

Наивный, но честный подход: линейный тренд (МНК) по недельным медианам ₸/м²
из price_history — отдельно по городу и по каждому району. Экстраполяция
на 13 и 26 недель вперёд. Данных пока мало (история копится с осени), поэтому
прогноз показывается с явной оговоркой и только там, где точек достаточно.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from krisha.config import DB_PATH
from krisha.stats import DISTRICT_RU, _weekly_trend

logger = logging.getLogger(__name__)

MAX_WEEKS = 26          # берём до полугода истории
MIN_POINTS = 6          # меньше недельных точек — прогноз не показываем
MIN_N_DISTRICT = 30     # мин. объявлений в неделю для районной медианы
HORIZONS = {"m3": 13, "m6": 26}  # недель вперёд

DISCLAIMER = (
    "Прогноз — линейная экстраполяция недельных медиан ₸/м², "
    "истории пока немного. Ориентир, а не инвестиционный совет."
)


def _fit_forecast(trend: list[dict]) -> dict | None:
    """Линейный тренд по точкам недельных медиан → прогноз на горизонты."""
    if len(trend) < MIN_POINTS:
        return None
    y = np.array([t["median_ppsm"] for t in trend], dtype=float)
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    current = float(y[-1])
    out = {
        "current_ppsm": int(current),
        "weeks_used": len(y),
        "slope_week_pct": round(slope / current * 100, 2),
    }
    for key, weeks in HORIZONS.items():
        pred = float(slope * (x[-1] + weeks) + intercept)
        pred = max(pred, 0.0)
        out[key] = {
            "ppsm": int(pred),
            "change_pct": round((pred - current) / current * 100, 1),
        }
    return out


def build_forecast(db_path: Path | str = DB_PATH) -> dict:
    """Прогноз по городу и районам: {city, districts: [...], disclaimer}."""
    city_trend = _weekly_trend(db_path, max_weeks=MAX_WEEKS)
    city = _fit_forecast(city_trend)

    districts = []
    for slug, ru in DISTRICT_RU.items():
        try:
            trend = _weekly_trend(
                db_path, max_weeks=MAX_WEEKS, min_n=MIN_N_DISTRICT, district=slug
            )
        except Exception:  # noqa: BLE001 — один район не должен ронять прогноз
            logger.exception("forecast: не удалось посчитать тренд %s", slug)
            continue
        fc = _fit_forecast(trend)
        if fc is not None:
            districts.append({"district": ru, "slug": slug, **fc})

    districts.sort(key=lambda d: d["m6"]["change_pct"], reverse=True)
    return {"city": city, "districts": districts, "disclaimer": DISCLAIMER}
