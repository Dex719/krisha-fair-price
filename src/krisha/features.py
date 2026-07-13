"""Очистка данных и фичи для модели. Используется и в обучении, и в предсказании."""

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from krisha.config import (
    ALMATY_BBOX,
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

# LLM-флаги описания (этап 5) как бинарные фичи: flag_pledge, flag_bargain, ...
# Ключи — словарь FLAGS_RU из llm_flags; flags_known=0 — анализа не было.
from krisha.llm_flags import FLAGS_RU  # noqa: E402

FLAG_FEATURES = [f"flag_{key}" for key in FLAGS_RU]

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
    "hex7_ppsm", "hex8_ppsm",      # медианная ₸/м² по гексагонам H3 (krisha.spatial)
]
# Вычисляются, но в модель не идут — абляция на честном сплите (group split по
# зданиям + dedup перевыставлений) показала, что они ухудшают метрики:
#   knn_ppsm/knn_n      — на train сосед = свой дом, на test его нет (leakage-mismatch)
#   flag_*/flags_known  — LLM-флаги описаний: шум, R² падает 0.79 → 0.76
#   district_mismatch   — нулевой эффект; остаётся как бейдж-предупреждение в predict
EXTRA_FEATURES = ["district_mismatch", "knn_ppsm", "knn_n", "flags_known", *FLAG_FEATURES]
ALL_FEATURES = NUM_FEATURES + CAT_FEATURES
TARGET = "log_price"
MISSING_CAT = "unknown"

# issue #108: санитарный контракт сырых числовых полей — вне диапазона
# считается битым парсингом/опечаткой и превращается в NaN, а не клипается
# (клип маскирует мусор под легитимное значение: 2109 → «через 3 года»,
# а не «дом ещё не построен»). CatBoost переваривает NaN нативно.
TOTAL_FLOORS_RANGE = (1, 100)
YEAR_BUILT_MIN = 1930          # верхняя граница — current_year() + 3, динамически
CEILING_RANGE = (2.0, 5.0)
ROOMS_RANGE = (1, 10)


def current_year() -> int:
    """Текущий год для building_age. Функция, а не константа: захардкоженный
    год после Нового года тихо сдвигал бы возраст домов у модели и статистики.
    Train и predict зовут одну функцию — сдвига train/serve не возникает
    (модель переобучается еженедельно)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).year


def _sanitize_raw_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Битые сырые значения → NaN, до расчёта производных фичей.

    issue #108: `total_floors=0` давал `floor_ratio=inf`; `floor > total_floors`
    и опечатки в `year_built` (1200, 20250, 2109) проходили без валидации и
    портили `is_last_floor`/`building_age`/`is_new_building`. Правило —
    невалидное значение превращаем в NaN, никогда не клипаем к границе.
    """
    df = df.copy()

    total_floors = df["total_floors"]
    bad_total = ~total_floors.between(*TOTAL_FLOORS_RANGE)
    df.loc[bad_total, "total_floors"] = np.nan

    floor = df["floor"]
    bad_floor = (floor < 1) | (floor > df["total_floors"])
    df.loc[bad_floor.fillna(False), "floor"] = np.nan

    year_max = current_year() + 3
    bad_year = ~df["year_built"].between(YEAR_BUILT_MIN, year_max)
    df.loc[bad_year, "year_built"] = np.nan

    bad_ceiling = ~df["ceiling"].between(*CEILING_RANGE)
    df.loc[bad_ceiling, "ceiling"] = np.nan

    bad_rooms = ~df["rooms"].between(*ROOMS_RANGE)
    df.loc[bad_rooms, "rooms"] = np.nan

    lat, lon = df["lat"], df["lon"]
    in_bbox = lat.between(ALMATY_BBOX["lat_min"], ALMATY_BBOX["lat_max"]) & lon.between(
        ALMATY_BBOX["lon_min"], ALMATY_BBOX["lon_max"]
    )
    had_coords = lat.notna() | lon.notna()
    bad_coords = ~in_bbox.fillna(False) & had_coords
    df.loc[bad_coords, ["lat", "lon"]] = np.nan

    return df


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


def _parse_flags_value(val: Any) -> list[str] | None:
    """Явное значение колонки llm_flags (list или JSON-строка) → list | None."""
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val:
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            return None
    return None


def add_llm_flag_features(df: pd.DataFrame) -> pd.DataFrame:
    """LLM-флаги описания → бинарные фичи flag_* + индикатор flags_known.

    Источник: колонка `llm_flags` (predict кладёт свежие флаги) или кэш в БД
    (train) — кэш читается ПАЧКОЙ одним соединением, а не по строке (раньше
    обучение делало 7000+ отдельных connect'ов к SQLite).
    Нет анализа → все нули и flags_known=0 — модель это различает.
    """
    df = df.copy()
    flags_list: list[list[str] | None] = [None] * len(df)
    pending: list[tuple[int, int, str]] = []  # (позиция, listing_id, описание)
    for pos, (_, row) in enumerate(df.iterrows()):
        parsed = _parse_flags_value(row.get("llm_flags"))
        if parsed is not None:
            flags_list[pos] = parsed
            continue
        lid, text = row.get("id"), row.get("description")
        if lid and isinstance(text, str) and len(text.strip()) >= 20:
            pending.append((pos, int(lid), text))

    if pending:
        from krisha.llm_flags import get_cached_flags_bulk

        try:
            cached = get_cached_flags_bulk([(lid, text) for _, lid, text in pending])
        except Exception:  # noqa: BLE001 — кэш недоступен → фичи по нулям
            cached = {}
        for pos, lid, _ in pending:
            flags_list[pos] = cached.get(lid)

    df["flags_known"] = [int(f is not None) for f in flags_list]
    for col, key in zip(FLAG_FEATURES, FLAGS_RU):
        df[col] = [int(key in f) if f else 0 for f in flags_list]
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
    spatial_ref: dict | None = None,
    knn_self_indices=None,
) -> pd.DataFrame:
    """Добавляет производные фичи. Работает и для одного объявления (predict).

    spatial_ref/knn_self_indices — референс пространственных фичей (train
    передаёт свежепостроенный по train-части и позиции строк в нём, predict —
    сохранённый в models/spatial_ref.json через krisha.spatial.load_spatial_ref).
    """
    from krisha.geo import add_geo_features
    from krisha.zones import resolve_zones

    # Чиним район/микрорайон по полигонам OSM (пропуски krisha, кривые зоны)
    df = resolve_zones(df)
    df = add_raw_param_features(df)
    df = add_complex_features(df, lookup=complex_lookup)
    df = add_geo_features(df)
    df = add_llm_flag_features(df)
    for col in ["rooms", "area", "floor", "total_floors", "year_built", "ceiling",
                "lat", "lon", "photos_count", *COMPLEX_NUM_FEATURES]:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = _sanitize_raw_numeric(df)  # issue #108: битые значения → NaN до фичей

    df["floor_ratio"] = df["floor"] / df["total_floors"]
    df["is_first_floor"] = (df["floor"] == 1).astype(int)
    df["is_last_floor"] = (df["floor"] == df["total_floors"]).astype(int)
    df["building_age"] = (current_year() - df["year_built"]).clip(lower=-5)
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

    # Пространственные фичи: гексагоны H3 + соседи (после district_ppsm — фолбэк)
    from krisha.spatial import add_spatial_features

    df = add_spatial_features(df, ref=spatial_ref, self_indices=knn_self_indices)

    if "district_mismatch" not in df:
        df["district_mismatch"] = 0

    if "price" in df:
        df["log_price"] = np.log1p(df["price"])

    # issue #108: страховка в конце пайплайна — любой inf, просочившийся через
    # деление/производные фичи (сейчас их не должно быть после сантизации выше,
    # но новые фичи неизбежно добавятся), не должен молча портить обучение.
    num_cols = [c for c in ALL_FEATURES if c in df.columns and c not in CAT_FEATURES]
    df[num_cols] = df[num_cols].replace([np.inf, -np.inf], np.nan)
    return df


def listing_to_frame(
    listing: dict[str, Any],
    ppsm_maps: dict | None = None,
    spatial_ref: dict | None = None,
) -> pd.DataFrame:
    """Один распарсенный listing-dict → DataFrame с фичами для предсказания."""
    return build_features(pd.DataFrame([listing]), ppsm_maps=ppsm_maps, spatial_ref=spatial_ref)
