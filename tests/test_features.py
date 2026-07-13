import numpy as np
import pandas as pd
import pytest

from krisha.features import ALL_FEATURES, build_features, clean, haversine_km, listing_to_frame


def make_df():
    return pd.DataFrame([
        {"price": 42_000_000, "area": 60, "rooms": 2, "floor": 4, "total_floors": 9,
         "year_built": 2012, "lat": 43.2284, "lon": 76.9123, "district": "Bostandykskiy_r-n",
         "ceiling": 2.8, "photos_count": 5},
        {"price": 100, "area": 60, "rooms": 1},          # мусорная цена → выкинуть
        {"price": 42_000_000, "area": None, "rooms": 1},  # нет площади → выкинуть
        {"price": 30_000_000, "area": 3, "rooms": 1},     # нереальная площадь → выкинуть
    ])


def test_clean_drops_garbage():
    df = clean(make_df())
    assert len(df) == 1
    assert df.iloc[0]["price"] == 42_000_000


def test_build_features_derived():
    df = build_features(clean(make_df()))
    row = df.iloc[0]
    assert row["floor_ratio"] == pytest.approx(4 / 9)
    assert row["is_first_floor"] == 0
    assert row["is_last_floor"] == 0
    assert row["building_age"] == 14  # 2026 - 2012
    assert 0 < row["dist_center_km"] < 10
    assert row["log_price"] == pytest.approx(17.553, abs=0.01)
    for col in ALL_FEATURES:
        assert col in df.columns


def test_categoricals_filled():
    df = build_features(pd.DataFrame([{"price": 30_000_000, "area": 50}]))
    assert df.iloc[0]["district"] == "unknown"
    assert df.iloc[0]["building_type"] == "unknown"


def test_listing_to_frame_single_row():
    df = listing_to_frame({"price": 30_000_000, "area": 50, "rooms": 2})
    assert len(df) == 1
    assert set(ALL_FEATURES) <= set(df.columns)


def test_haversine_known_distance():
    # Алматы → Астана ≈ 970 км
    d = haversine_km(43.238949, 76.889709, 51.169392, 71.449074)
    assert 900 < d < 1050


# ---------- этап 1: фичи из raw_params ----------

RAW_PARAMS_JSON = (
    '{"flat.renovation": "Свежий ремонт", "flat.toilet": "совмещенный", '
    '"live.furniture": "полностью", "flat.parking": "паркинг", '
    '"flat.balcony": "лоджия", '
    '"flat.security": "охрана, домофон, видеонаблюдение, видеодомофон"}'
)


def test_raw_params_extracted():
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "raw_params": RAW_PARAMS_JSON},
    ]))
    row = df.iloc[0]
    assert row["renovation"] == "свежий ремонт"  # нормализован регистр
    assert row["toilet"] == "совмещенный"
    assert row["furniture"] == "полностью"
    assert row["parking"] == "паркинг"
    assert row["balcony"] == "лоджия"
    assert row["has_security_guard"] == 1
    assert row["has_intercom"] == 1
    assert row["has_video_surveillance"] == 1
    assert row["security_count"] == 4


def test_raw_params_missing_is_unknown():
    df = build_features(pd.DataFrame([{"price": 30_000_000, "area": 50}]))
    row = df.iloc[0]
    assert row["renovation"] == "unknown"
    assert row["toilet"] == "unknown"
    assert row["has_security_guard"] == 0
    assert row["security_count"] == 0


def test_raw_params_broken_json_safe():
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "raw_params": "{broken"},
    ]))
    assert df.iloc[0]["renovation"] == "unknown"


def test_listing_to_frame_with_raw_params():
    df = listing_to_frame({"price": 30_000_000, "area": 50, "rooms": 2,
                           "raw_params": RAW_PARAMS_JSON})
    assert df.iloc[0]["renovation"] == "свежий ремонт"
    assert set(ALL_FEATURES) <= set(df.columns)


# ---------- issue #108: санитарный контракт числовых полей ----------


def test_floor_ratio_nan_when_total_floors_zero():
    """Раньше total_floors=0 давал floor_ratio=inf (битый парсинг этажности)."""
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "floor": 5, "total_floors": 0},
    ]))
    row = df.iloc[0]
    assert pd.isna(row["floor_ratio"])
    assert not np.isinf(row["floor_ratio"])
    assert pd.isna(row["total_floors"])


def test_floor_exceeding_total_floors_becomes_nan():
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "floor": 15, "total_floors": 9},
    ]))
    row = df.iloc[0]
    assert pd.isna(row["floor"])
    assert row["total_floors"] == 9
    assert pd.isna(row["floor_ratio"])
    assert row["is_last_floor"] == 0


def test_year_built_typo_does_not_fake_new_building():
    """issue #108: year_built=2109 раньше клипался в building_age=-5 → ложный is_new_building."""
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "year_built": 2109},
    ]))
    row = df.iloc[0]
    assert pd.isna(row["year_built"])
    assert pd.isna(row["building_age"])
    assert row["is_new_building"] == 0


def test_year_built_zero_sanitized_to_nan():
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "year_built": 0},
    ]))
    assert pd.isna(df.iloc[0]["year_built"])


def test_ceiling_out_of_range_sanitized_to_nan():
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "ceiling": 10.0},
    ]))
    assert pd.isna(df.iloc[0]["ceiling"])


def test_rooms_out_of_range_sanitized_to_nan():
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "rooms": 25},
    ]))
    assert pd.isna(df.iloc[0]["rooms"])


def test_coords_outside_almaty_bbox_sanitized_to_nan():
    """issue #104/#108: координаты чужого города (Астана) → NaN, не портят dist_center_km."""
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "lat": 51.169392, "lon": 71.449074},
    ]))
    row = df.iloc[0]
    assert pd.isna(row["lat"])
    assert pd.isna(row["lon"])
    assert pd.isna(row["dist_center_km"])


def test_coords_inside_almaty_bbox_kept():
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "lat": 43.2284, "lon": 76.9123},
    ]))
    row = df.iloc[0]
    assert row["lat"] == pytest.approx(43.2284)
    assert row["lon"] == pytest.approx(76.9123)


def test_build_features_no_inf_leaks_through():
    """Страховка в конце build_features: любой inf в числовых фичах → NaN."""
    df = build_features(pd.DataFrame([
        {"price": 30_000_000, "area": 50, "floor": 5, "total_floors": 0,
         "year_built": 20250, "ceiling": -3},
    ]))
    num_cols = [c for c in ALL_FEATURES if pd.api.types.is_numeric_dtype(df[c])]
    assert not np.isinf(df[num_cols].to_numpy(dtype=float)).any()
