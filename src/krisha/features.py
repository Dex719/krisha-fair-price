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
from krisha.knn import KNN_FEATURES  # Задача 2.1: медианная ₸/м² ближайших соседей

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
    *KNN_FEATURES,
]
ALL_FEATURES = NUM_FEATURES + CAT_FEATURES
# Таргет — log(цена за м²), а не log(полной цены). Цена за м² куда менее растянута
# (≈100k–5M ₸/м² против 5М–1.5млрд ₸), дисперсия стабильнее, и RMSE на лог-таргете
# ≈ оптимизация относительной ошибки → согласовано с MAPE. `area` остаётся в фичах:
# ₸/м² нелинейно зависит от площади (у студий выше, у больших — ниже).
TARGET = "log_ppm2"
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


def _fingerprint_series(df: pd.DataFrame) -> pd.Series:
    """Векторный геоотпечаток квартиры (без цены): район+комнаты+площадь(0.5)+
    этаж/этажность+координаты(~10 м). Совпадает с db.listing_fingerprint по полям.
    Перезалив того же объекта даёт тот же отпечаток. None, если нет area/lat/lon.
    """
    area = pd.to_numeric(df.get("area"), errors="coerce")
    lat = pd.to_numeric(df.get("lat"), errors="coerce")
    lon = pd.to_numeric(df.get("lon"), errors="coerce")
    district = df.get("district", pd.Series([""] * len(df), index=df.index))
    rooms = df.get("rooms", pd.Series([""] * len(df), index=df.index))
    floor = df.get("floor", pd.Series([""] * len(df), index=df.index))
    total = df.get("total_floors", pd.Series([""] * len(df), index=df.index))

    def _fp(i: Any) -> str | None:
        a, la, lo = area.get(i), lat.get(i), lon.get(i)
        if pd.isna(a) or a == 0 or pd.isna(la) or pd.isna(lo):
            return None
        d = str(district.get(i) or "").lower().strip()
        return "|".join((
            d,
            "" if pd.isna(rooms.get(i)) else str(rooms.get(i)),
            f"{round(float(a) * 2) / 2:.1f}",
            "" if pd.isna(floor.get(i)) else str(floor.get(i)),
            "" if pd.isna(total.get(i)) else str(total.get(i)),
            f"{float(la):.4f}",
            f"{float(lo):.4f}",
        ))

    return pd.Series([_fp(i) for i in df.index], index=df.index)


def dedup(df: pd.DataFrame) -> pd.DataFrame:
    """Схлопывает перезаливы одной квартиры по геоотпечатку (без учёта цены).

    Защита от утечки таргета в KNN: один физический объект не должен попадать
    в индекс соседей дважды (иначе «сосед» с той же ценой) и не должен
    одновременно оказаться в train и test. Из группы дублей оставляем самое
    свежее объявление (по last_seen, если колонка есть). Строки без отпечатка
    (нет координат/площади) не трогаем.
    """
    df = df.copy()
    fp = _fingerprint_series(df)
    has = fp.notna()
    if not has.any():
        return df.reset_index(drop=True)
    sub = df.loc[has].copy()
    sub["_fp"] = fp[has]
    if "last_seen" in sub:
        sub = sub.sort_values("last_seen")
    drop_idx = sub.index[sub["_fp"].duplicated(keep="last")]
    return df.drop(index=drop_idx).reset_index(drop=True)


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


def add_knn_features(df: pd.DataFrame, knn_index=None, knn_self: bool = False) -> pd.DataFrame:
    """Фичи KNN-цены соседей (Задача 2.1). Нет индекса/координат → NaN-fallback.

    knn_self=True — строки сами лежат в индексе (train): первый сосед = сама
    точка, его выкидываем. В predict индекс берётся из models/knn_index.npz.
    """
    df = df.copy()
    if knn_index is None:
        from krisha.knn import load_default_knn_index

        knn_index = load_default_knn_index()
    if knn_index is None:
        for col in KNN_FEATURES:
            if col not in df:
                df[col] = np.nan
        return df
    lat = df["lat"] if "lat" in df else pd.Series(np.nan, index=df.index)
    lon = df["lon"] if "lon" in df else pd.Series(np.nan, index=df.index)
    knn_ppm2, knn_dist = knn_index.query(lat, lon, self_neighbor=knn_self)
    df["knn_ppm2"] = knn_ppm2
    df["knn_dist_km"] = knn_dist
    return df


def build_features(
    df: pd.DataFrame,
    ppsm_maps: dict | None = None,
    complex_lookup: dict | None = None,
    knn_index=None,
    knn_self: bool = False,
) -> pd.DataFrame:
    """Добавляет производные фичи. Работает и для одного объявления (predict)."""
    from krisha.geo import add_geo_features

    df = add_raw_param_features(df)
    df = add_complex_features(df, lookup=complex_lookup)
    df = add_geo_features(df)
    df = add_knn_features(df, knn_index=knn_index, knn_self=knn_self)
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
        # Цена за м² и её лог — основной таргет обучения (см. TARGET).
        # area уже приведена к числу выше; защищаемся от нулей/NaN.
        with np.errstate(divide="ignore", invalid="ignore"):
            df["ppm2"] = df["price"] / df["area"]
        df["log_ppm2"] = np.log1p(df["ppm2"])
    return df


def smearing_factor(y_log_true: Any, y_log_pred: Any) -> float:
    """Smearing-оценка Дуана для коррекции лог-смещения при обратном переходе.

    При обучении на лог-таргете наивный `expm1(pred)` даёт *смещённую вниз* оценку
    в ₸ (неравенство Йенсена): среднее лог-ошибки = 0, но среднее exp(ошибки) > 1.
    Множитель S = mean(exp(residual)) непараметрически снимает это смещение.

    Считать ТОЛЬКО на train-остатках. ppm2 ≥ 100k ≫ 1, поэтому log1p ≈ log и
    мультипликативная коррекция корректна.
    """
    resid = np.asarray(y_log_true, dtype=float) - np.asarray(y_log_pred, dtype=float)
    resid = resid[np.isfinite(resid)]
    if resid.size == 0:
        return 1.0
    return float(np.mean(np.exp(resid)))


def reconstruct_price(log_ppm2_pred: Any, area: Any, smearing: float = 1.0) -> np.ndarray:
    """log(цена/м²)-предсказание → полная цена ₸ с smearing-коррекцией.

    Единая точка обратного преобразования для train и predict, чтобы они не
    разъезжались: ppm2 = expm1(pred) * smearing, price = ppm2 * area.
    """
    ppm2 = np.expm1(np.asarray(log_ppm2_pred, dtype=float)) * float(smearing)
    return ppm2 * np.asarray(area, dtype=float)


def listing_to_frame(listing: dict[str, Any], ppsm_maps: dict | None = None) -> pd.DataFrame:
    """Один распарсенный listing-dict → DataFrame с фичами для предсказания."""
    return build_features(pd.DataFrame([listing]), ppsm_maps=ppsm_maps)
