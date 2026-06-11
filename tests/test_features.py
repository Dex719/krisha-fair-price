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
