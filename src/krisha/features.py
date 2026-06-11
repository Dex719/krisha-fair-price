"""Очистка данных и фичи для модели. Используется и в обучении, и в предсказании."""

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

CAT_FEATURES = ["district", "microdistrict", "building_type", "complex_name", "user_type", "category"]
NUM_FEATURES = [
    "rooms", "area", "floor", "total_floors", "floor_ratio", "is_first_floor",
    "is_last_floor", "year_built", "building_age", "ceiling", "lat", "lon",
    "dist_center_km", "photos_count",
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


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет производные фичи. Работает и для одного объявления (predict)."""
    df = df.copy()
    for col in ["rooms", "area", "floor", "total_floors", "year_built", "ceiling",
                "lat", "lon", "photos_count"]:
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

    if "price" in df:
        df["log_price"] = np.log1p(df["price"])
    return df


def listing_to_frame(listing: dict[str, Any]) -> pd.DataFrame:
    """Один распарсенный listing-dict → DataFrame с фичами для предсказания."""
    return build_features(pd.DataFrame([listing]))
