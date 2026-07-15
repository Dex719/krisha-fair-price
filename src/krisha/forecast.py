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
MIN_WEEKS_M6 = 12       # меньше недель истории — горизонт m6 прячем (низкая надёжность)
MIN_N_DISTRICT = 30     # мин. объявлений в неделю для районной медианы
HORIZONS = {"m3": 13, "m6": 26}  # недель вперёд
SLOPE_Z95 = 1.96        # z-квантиль для 95% доверительного интервала наклона

DISCLAIMER = (
    "Прогноз — линейная экстраполяция недельных медиан ₸/м², "
    "истории пока немного. Ориентир, а не инвестиционный совет."
)


def _fit_forecast(trend: list[dict]) -> dict | None:
    """Линейный тренд по точкам недельных медиан → прогноз на горизонты.

    X — реальный календарный номер недели (`t` из `_weekly_trend`), а не
    порядковый индекс точки: если недели пропущены (мало объявлений —
    `_weekly_trend` их просто не добавляет), их разрыв должен остаться
    разрывом в X, иначе экстраполяция считает соседние по списку, но
    удалённые по календарю точки смежными неделями и искажает наклон.
    """
    if len(trend) < MIN_POINTS:
        return None
    y = np.array([t["median_ppsm"] for t in trend], dtype=float)
    x = np.array([t["t"] for t in trend], dtype=float)
    current = float(y[-1])
    if y.max() == y.min():
        # Все недельные медианы совпадают: реальный наклон точно 0.
        # cov=True на вырожденном (нулевая дисперсия остатков) фите даёт
        # slope/se порядка float-эпсилон — их отношение шумовое и может
        # случайно превысить порог значимости. Считаем детерминированно.
        slope, intercept, slope_se = 0.0, current, 0.0
        slope_significant = False
    else:
        (slope, intercept), cov = np.polyfit(x, y, 1, cov=True)
        slope_se = float(np.sqrt(cov[0, 0]))
        # Наклон "не отличим от нуля", если 0 лежит внутри его 95% ДИ.
        slope_significant = abs(slope) > SLOPE_Z95 * slope_se
    out = {
        "current_ppsm": int(current),
        "weeks_used": len(y),
        "slope_week_pct": round(slope / current * 100, 2),
        "slope_significant": bool(slope_significant),
    }
    for key, weeks in HORIZONS.items():
        if key == "m6" and len(y) < MIN_WEEKS_M6:
            continue  # мало истории — полугодовой горизонт не показываем
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
