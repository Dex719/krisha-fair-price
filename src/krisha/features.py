"""Очистка данных и фичи для модели. Используется и в обучении, и в предсказании."""

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from krisha.config import (
    ALMATY_CENTER,
    AREA_MAX,
    AREA_MIN,
    PPSM_MAX,
    PPSM_MIN,
    PRICE_MAX,
    PRICE_MIN,
)
from krisha.geo import GEO_FEATURES  # этап 3: расстояния до POI + walk_score

# Поля из raw_params (этап 1 роадмапа): имя фичи → ключ в JSON объявления.
# Значения — стандартные опции krisha («свежий ремонт», «совмещенный», ...),
# пропуск → MISSING_CAT, CatBoost обрабатывает её как обычную категорию.
RAW_PARAM_CAT_MAP = {
    "renovation": "flat.renovation",   # состояние ремонта — главный прирост
    "toilet": "flat.toilet",           # санузел
    "furniture": "live.furniture",     # мебель
    "parking": "flat.parking",         # парковка
    "balcony": "flat.balcony",         # балкон/лоджия — тот же механизм, бесплатно
}
# flat.security — список через запятую («охрана, домофон, видеонаблюдение»):
# раскладываем в бинарные флаги + общий счётчик опций.
SECURITY_FLAGS = {
    "has_security_guard": "охрана",
    "has_intercom": "домофон",
    "has_video_surveillance": "видеонаблюдение",
}

# Фичи из справочника ЖК (этап 2 роадмапа): подмешиваются по имени комплекса
# через models/complexes.json (см. krisha.complexes). Нет ЖК → unknown/NaN.
COMPLEX_CAT_FEATURES = ["housing_class", "developer"]
COMPLEX_NUM_FEATURES = ["completion_year", "apartments_count"]

CAT_FEATURES = [
    "district", "microdistrict", "building_type", "complex_name", "user_type", "category",
    *RAW_PARAM_CAT_MAP,
    *COMPLEX_CAT_FEATURES,
]
NUM_FEATURES = [
    "rooms", "area", "floor", "total_floors", "floor_ratio", "is_first_floor",
    "is_last_floor", "year_built", "building_age", "ceiling", "lat", "lon",
    "dist_center_km", "photos_count", "is_new_building",
    "district_ppsm", "microdistrict_ppsm",
    *SECURITY_FLAGS, "security_count",
    *COMPLEX_NUM_FEATURES,
    *GEO_FEATURES,
]
ALL_FEATURES = NUM_FEATURES + CAT_FEATURES
TARGET = "log_price"
CURRENT_YEAR = 2026
MISSING_CAT = "unknown"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками в км."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Убирает мусор: без цены/площади, нереальные цены и цены за м²."""
    df = df.dropna(subset=["price", "area"]).copy()
    df = df[(df["price"] >= PRICE_MIN) & (df["price"] <= PRICE_MAX)]
    df = df[(df["area"] >= AREA_MIN) & (df["area"] <= AREA_MAX)]
    ppsm = df["price"] / df["area"]
    df = df[(ppsm >= PPSM_MIN) & (ppsm <= PPSM_MAX)]
    return df.reset_index(drop=True)


def compute_ppsm_maps(df: pd.DataFrame) -> dict:
    """Медианная цена за м² по районам/микрорайонам (считать ТОЛЬКО на train-части).

    Возвращаемый dict сохраняется в model_meta.json и используется в predict.
    """
    sub = df.dropna(subset=["price", "area"]).copy()
    sub = sub[sub["area"] > 0]
    sub["ppsm"] = sub["price"] / sub["area"]
    for col in ("district", "microdistrict"):
        if col not in sub:
            sub[col] = MISSING_CAT
    district = sub["district"].fillna(MISSING_CAT).astype(str)
    micro = sub["microdistrict"].fillna(MISSING_CAT).astype(str)
    return {
        "district": sub.groupby(district)["ppsm"].median().to_dict(),
        "microdistrict": sub.groupby(micro)["ppsm"].median().to_dict(),
        "global": float(sub["ppsm"].median()),
    }


def _parse_raw_params(value: Any) -> dict:
    """JSON-строка raw_params → dict; мусор и пропуски → пустой dict."""
    if isinstance(value, dict):
        return value
    if not value or not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _norm_cat(value: Any) -> str:
    """Нормализация категориального значения: регистр, пробелы, пропуск → MISSING_CAT."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return MISSING_CAT
    text = " ".join(str(value).split()).strip().lower()
    return text or MISSING_CAT


def add_raw_param_features(df: pd.DataFrame) -> pd.DataFrame:
    """Фичи из raw_params: ремонт, санузел, мебель, парковка, балкон, безопасность.

    Работает в двух режимах:
    - есть колонка `raw_params` (train из БД, predict по URL) — парсим JSON;
    - колонки уже заданы напрямую (ручной ввод) — только нормализуем.
    """
    df = df.copy()
    raw = (
        df["raw_params"].map(_parse_raw_params)
        if "raw_params" in df
        else pd.Series([{}] * len(df), index=df.index)
    )

    for col, key in RAW_PARAM_CAT_MAP.items():
        if col not in df:
            df[col] = raw.map(lambda p, k=key: p.get(k))
        df[col] = df[col].map(_norm_cat)

    if "security" not in df:
        df["security"] = raw.map(lambda p: p.get("flat.security") or "")
    sec = df["security"].fillna("").astype(str).str.lower()
    for col, keyword in SECURITY_FLAGS.items():
        df[col] = sec.str.contains(keyword, regex=False).astype(int)
    df["security_count"] = sec.map(lambda s: len([x for x in s.split(",") if x.strip()]))
    return df


def complex_join_name(df: pd.DataFrame) -> pd.Series:
    """Имя ЖК для джойна: raw_params["map.complex"] (точнее), иначе complex_name."""
    raw = (
        df["raw_params"].map(_parse_raw_params)
        if "raw_params" in df
        else pd.Series([{}] * len(df), index=df.index)
    )
    name = raw.map(lambda p: p.get("map.complex"))
    if "complex_name" in df:
        name = name.fillna(df["complex_name"])
    return name


def add_complex_features(df: pd.DataFrame, lookup: dict | None = None) -> pd.DataFrame:
    """Атрибуты ЖК из справочника: застройщик, класс жилья, год сдачи, размер ЖК."""
    from krisha.complexes import load_complex_lookup, lookup_complex_attrs

    df = df.copy()
    if lookup is None:
        lookup = load_complex_lookup()
    attrs = complex_join_name(df).map(lambda n: lookup_complex_attrs(n, lookup))
    for col in COMPLEX_CAT_FEATURES + COMPLEX_NUM_FEATURES:
        if col not in df:
            df[col] = attrs.map(lambda a, c=col: a.get(c))
    return df


def build_features(
    df: pd.DataFrame,
    ppsm_maps: dict | None = None,
    complex_lookup: dict | None = None,
) -> pd.DataFrame:
    """Добавляет производные фичи. Работает и для одного объявления (predict)."""
    from krisha.geo import add_geo_features
    from krisha.zones import resolve_zones

    # Чиним район/микрорайон по полигонам OSM (пропуски krisha, кривые зоны)
    df = resolve_zones(df)
    df = add_raw_param_features(df)
    df = add_complex_features(df, lookup=complex_lookup)
    df = add_geo_features(df)
    for col in ["rooms", "area", "floor", "total_floors", "year_built", "ceiling",
                "lat", "lon", "photos_count", *COMPLEX_NUM_FEATURES]:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["floor_ratio"] = df["floor"] / df["total_floors"]
    df["is_first_floor"] = (df["floor"] == 1).astype(int)
    df["is_last_floor"] = (df["floor"] == df["total_floors"]).astype(int)
    df["building_age"] = (CURRENT_YEAR - df["year_built"]).clip(lower=-5)
    df["dist_center_km"] = [
        haversine_km(lat, lon, *ALMATY_CENTER) if pd.notna(lat) and pd.notna(lon) else np.nan
        for lat, lon in zip(df["lat"], df["lon"])
    ]

    for col in CAT_FEATURES:
        if col not in df:
            df[col] = MISSING_CAT
        df[col] = df[col].fillna(MISSING_CAT).astype(str)

    # Новостройка vs вторичка: категория krisha + свежий год постройки + продаёт ЖК
    df["is_new_building"] = (
        (df["category"] == "novostroiki")
        | (df["building_age"] <= 1)
        | (df["user_type"] == "complex")
    ).astype(int)

    # Медианная ₸/м² по району и микрорайону (из train-статистики, без утечки)
    maps = ppsm_maps or {}
    d_map, m_map = maps.get("district", {}), maps.get("microdistrict", {})
    global_ppsm = maps.get("global", np.nan)
    df["district_ppsm"] = df["district"].map(d_map).fillna(global_ppsm)
    df["microdistrict_ppsm"] = df["microdistrict"].map(m_map).fillna(df["district_ppsm"])

    if "price" in df:
        df["log_price"] = np.log1p(df["price"])
    return df


def listing_to_frame(listing: dict[str, Any], ppsm_maps: dict | None = None) -> pd.DataFrame:
    """Один распарсенный listing-dict → DataFrame с фичами для предсказания."""
    return build_features(pd.DataFrame([listing]), ppsm_maps=ppsm_maps)
