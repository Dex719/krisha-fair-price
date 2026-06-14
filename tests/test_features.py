import numpy as np
import pandas as pd
import pytest

from krisha.features import (
    ALL_FEATURES,
    TARGET,
    build_features,
    clean,
    haversine_km,
    listing_to_frame,
    reconstruct_price,
    smearing_factor,
)


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


# ---------- таргет log(цена/м²) + smearing ----------

def test_target_is_log_ppm2():
    assert TARGET == "log_ppm2"


def test_build_features_log_ppm2():
    df = build_features(clean(make_df()))
    row = df.iloc[0]
    # 42_000_000 / 60 = 700_000 ₸/м²
    assert row["ppm2"] == pytest.approx(700_000)
    assert row["log_ppm2"] == pytest.approx(np.log1p(700_000))


def test_reconstruct_price_roundtrip():
    # log(ppm2) → полная цена через area; smearing=1 → точный обратный переход
    log_ppm2 = np.log1p(np.array([700_000.0, 1_000_000.0]))
    area = np.array([60.0, 50.0])
    price = reconstruct_price(log_ppm2, area, smearing=1.0)
    assert price[0] == pytest.approx(42_000_000, rel=1e-6)
    assert price[1] == pytest.approx(50_000_000, rel=1e-6)


def test_reconstruct_price_applies_smearing():
    base = reconstruct_price(np.log1p(700_000.0), 60.0, smearing=1.0)
    bumped = reconstruct_price(np.log1p(700_000.0), 60.0, smearing=1.05)
    assert bumped == pytest.approx(base * 1.05, rel=1e-6)


def test_smearing_factor_no_residuals_is_one():
    y = np.array([1.0, 2.0, 3.0])
    assert smearing_factor(y, y) == pytest.approx(1.0)


def test_smearing_factor_positive_for_spread_residuals():
    # E[exp(resid)] > exp(E[resid]) при ненулевой дисперсии (неравенство Йенсена)
    true = np.array([1.0, 1.0, 1.0, 1.0])
    pred = np.array([0.5, 1.5, 0.5, 1.5])  # средний остаток = 0
    assert smearing_factor(true, pred) > 1.0


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
