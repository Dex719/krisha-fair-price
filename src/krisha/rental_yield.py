"""Доходность сдачи в аренду для объявления о продаже.

Продажный лот прогоняется через модель аренды (krisha.model_spec.RENT) как
будто он сдаётся: параметры продажной страницы переводятся на словарь
арендной (ремонт, санузел, балкон), а признаков, которых у продажи нет вовсе
(техника, «кому сдаётся»), — типичными значениями арендного рынка. Получаем
ожидаемую аренду с интервалом и валовую доходность к цене:

    доходность = 12 × аренда / цена квартиры

Это валовая доходность по ценам объявлений: без налога, простоя, коммуналки
и ремонта. Для ориентира рядом — та же величина по медианам района (продажа и
аренда из своих баз), чтобы было видно, лучше лот рынка или хуже.

Квартира в черновой отделке так не сдаётся — для неё аренду оцениваем «после
ремонта» и честно об этом пишем (assumes_renovation).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from functools import lru_cache
from typing import Any

from krisha.config import (
    DB_PATH,
    PPSM_MAX,
    PPSM_MIN,
    PRICE_MAX,
    PRICE_MIN,
    RENT_DB_PATH,
    RENT_PPSM_MAX,
    RENT_PPSM_MIN,
    RENT_PRICE_MAX,
    RENT_PRICE_MIN,
    RENT_SPATIAL_REF_PATH,
)

logger = logging.getLogger(__name__)

# Словарь продажной страницы → словарь арендной (значения krisha, октябрь 2026)
RENOVATION_TO_RENT = {
    "свежий ремонт": "свежий ремонт, новая мебель",
    "не новый, но аккуратный ремонт": "не новый, но аккуратный и чистый ремонт",
    "требует ремонта": "без ремонта",
}
ROUGH_FINISH = {"черновая отделка", "свободная планировка"}
TOILET_TO_RENT = {
    "совмещенный": ("совмещен", 1),
    "раздельный": ("разделен", 1),
    "2 с/у и более": ("разделен, совмещен", 2),
}
BALCONY_TO_COUNTS = {  # (балконов, лоджий)
    "балкон": (1, 0),
    "лоджия": (0, 1),
    "балкон и лоджия": (1, 1),
    "несколько балконов или лоджий": (2, 0),
}
# Продажные ключи, которые модель аренды прочла бы раньше арендных
# (features.RAW_PARAM_FALLBACKS: продажный ключ первым) — убираем.
SALE_ONLY_KEYS = ("flat.renovation", "flat.toilet", "flat.balcony")

# Типичные значения арендных признаков (медиана/мода базы аренды, октябрь
# 2026). Мета модели аренды хранит свежие (feature_defaults) — эти на случай
# меты без них.
FALLBACK_DEFAULTS: dict[str, Any] = {
    "balcony_n": 1.0, "loggia_n": 0.0, "toilet_count": 1.0, "kitchen_studio": 0.0,
    "n_facilities": 7.0, "n_furniture_items": 5.0,
    "fac_aircon": 1.0, "fac_dishwasher": 0.0, "fac_elevator": 0.0, "fac_internet": 1.0,
    "fac_tv": 1.0, "fac_storage": 0.0,
    "who_kids": 0.0, "who_pets": 0.0, "who_family": 1.0, "who_single": 1.0, "who_nonsmoke": 1.0,
    "bathroom": "ванна", "window_side": "во двор", "priv_dorm": "нет",
}

# Медиана района считается по свежим лотам (последние DISTRICT_WINDOW_DAYS),
# не меньше DISTRICT_MIN_N объявлений с каждой стороны — иначе шум.
DISTRICT_WINDOW_DAYS = 60
DISTRICT_MIN_N = 30
DISTRICT_CACHE_S = 6 * 3600


def as_rental(listing: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Продажный лот → тот же лот на словаре арендной страницы.

    Возвращает (лот, assumes_renovation). Цена убирается: она продажная.
    """
    try:
        params = json.loads(listing.get("raw_params") or "{}")
    except (TypeError, ValueError):
        params = {}
    if not isinstance(params, dict):
        params = {}
    renovation = params.get("flat.renovation")
    toilet = params.get("flat.toilet")
    balcony = params.get("flat.balcony")
    rough = renovation in ROUGH_FINISH

    rent_params = {k: v for k, v in params.items() if k not in SALE_ONLY_KEYS}
    rent_renovation = "свежий ремонт, новая мебель" if rough else RENOVATION_TO_RENT.get(renovation)
    if rent_renovation:
        rent_params["flat.rent_renovation"] = rent_renovation
    if rough:
        # после ремонта квартиру сдают с мебелью
        rent_params["live.furniture"] = "полностью"
    if toilet in TOILET_TO_RENT:
        rent_params["separated_toilet"], count = TOILET_TO_RENT[toilet]
        rent_params["toilet_count"] = str(count)
    if balcony in BALCONY_TO_COUNTS:
        b, lg = BALCONY_TO_COUNTS[balcony]
        rent_params["balcony_count"], rent_params["loggia_count"] = str(b), str(lg)
    adapted = {**listing, "raw_params": json.dumps(rent_params, ensure_ascii=False), "price": None}
    return adapted, rough


def _fill_typical(df, defaults: dict[str, Any]):
    from krisha.features import MISSING_CAT

    for col, value in defaults.items():
        if col not in df:
            continue
        if isinstance(value, str):
            df[col] = df[col].where(df[col] != MISSING_CAT, value)
        else:
            df[col] = df[col].fillna(value)
    return df


def estimate(listing: dict[str, Any], sale_price: float | None) -> dict[str, Any] | None:
    """Ожидаемая аренда и валовая доходность продажного лота. None — нет
    модели аренды (fail-soft: блок доходности просто не показывается)."""
    from catboost import Pool

    from krisha.features import listing_to_frame, model_center
    from krisha.predict import _price_pool, _round_price, load_model
    from krisha.spatial import load_spatial_ref

    try:
        model, meta = load_model("arenda")
    except FileNotFoundError:
        return None
    adapted, rough = as_rental(listing)
    df = listing_to_frame(
        adapted, ppsm_maps=meta.get("ppsm_maps"), spatial_ref=load_spatial_ref(RENT_SPATIAL_REF_PATH),
        center=model_center(meta),
    )
    df = _fill_typical(df, {**FALLBACK_DEFAULTS, **meta.get("feature_defaults", {})})
    pool = Pool(df[meta["features"]], cat_features=meta["cat_features"])
    row = _price_pool(pool, [None], meta, model, deal="arenda")[0]
    monthly, low, high = row["fair_price"], row["fair_low"], row["fair_high"]

    result: dict[str, Any] = {
        "monthly_rent": _round_price(monthly, "arenda"),
        "monthly_rent_low": _round_price(low, "arenda") if low is not None else None,
        "monthly_rent_high": _round_price(high, "arenda") if high is not None else None,
        "assumes_renovation": rough,
        "gross_yield_pct": None,
        "gross_yield_low_pct": None,
        "gross_yield_high_pct": None,
        "payback_years": None,
        "district_yield_pct": None,
        "district_yield_scope": None,
    }
    if sale_price and sale_price > 0:
        def pct(rent):
            return round(12 * rent / sale_price * 100, 1)

        result["gross_yield_pct"] = pct(monthly)
        result["gross_yield_low_pct"] = pct(low) if low is not None else None
        result["gross_yield_high_pct"] = pct(high) if high is not None else None
        result["payback_years"] = round(sale_price / (12 * monthly), 1) if monthly > 0 else None
    context = district_yield(listing.get("district"), listing.get("rooms"))
    if context:
        result["district_yield_pct"], result["district_yield_scope"] = context
    return result


def district_yield(district: str | None, rooms: Any) -> tuple[float, str] | None:
    """Валовая доходность по медианам района: 12 × ₸/м² аренды / ₸/м² продажи.

    Сначала район × комнаты (однушки доходнее), не набралось — весь район.
    Кэш на DISTRICT_CACHE_S: базы меняются раз в сутки.
    """
    if not district:
        return None
    table = _district_ppsm_table(int(time.time() // DISTRICT_CACHE_S))
    try:
        rooms_key = min(int(rooms), 4)
    except (TypeError, ValueError):
        rooms_key = None
    candidates = [((district, None), "district")]
    if rooms_key is not None:
        candidates.insert(0, ((district, rooms_key), "district_rooms"))
    for key, scope in candidates:
        sale, rent = table["sale"].get(key), table["rent"].get(key)
        if sale and rent and sale[1] >= DISTRICT_MIN_N and rent[1] >= DISTRICT_MIN_N:
            return round(12 * rent[0] / sale[0] * 100, 1), scope
    return None


@lru_cache(maxsize=2)
def _district_ppsm_table(_bucket: int) -> dict[str, dict]:
    """{"sale"|"rent": {(район, комнаты|None): (медиана ₸/м², n)}} по свежим лотам."""
    return {
        "sale": _ppsm_medians(DB_PATH, (PRICE_MIN, PRICE_MAX), (PPSM_MIN, PPSM_MAX)),
        "rent": _ppsm_medians(RENT_DB_PATH, (RENT_PRICE_MIN, RENT_PRICE_MAX), (RENT_PPSM_MIN, RENT_PPSM_MAX)),
    }


def _ppsm_medians(db_path, price_bounds, ppsm_bounds) -> dict:
    from statistics import median

    if not db_path.exists():
        return {}
    query = (
        "SELECT district, rooms, price * 1.0 / area FROM listings "
        "WHERE price BETWEEN ? AND ? AND area >= 10 AND district IS NOT NULL "
        "AND last_seen >= datetime('now', ?)"
    )
    from krisha.db import get_conn

    try:
        with get_conn(db_path) as conn:
            rows = conn.execute(query, (*price_bounds, f"-{DISTRICT_WINDOW_DAYS} days")).fetchall()
    except sqlite3.Error:
        logger.warning("Доходность района: не удалось прочитать %s", db_path, exc_info=True)
        return {}
    groups: dict = {}
    for district, rooms, ppsm in rows:
        if ppsm is None or not ppsm_bounds[0] <= ppsm <= ppsm_bounds[1]:
            continue
        groups.setdefault((district, None), []).append(ppsm)
        if rooms:
            groups.setdefault((district, min(int(rooms), 4)), []).append(ppsm)
    return {key: (median(values), len(values)) for key, values in groups.items()}
