"""Тесты spatial.py: гексагоны, KNN spatial lag, фолбэки, дедуп и сплит."""

import numpy as np
import pandas as pd

from krisha.spatial import (
    KNN_MIN_N,
    add_spatial_features,
    build_spatial_ref,
    self_indices_for,
)
from krisha.train import building_groups, dedup_relistings

rng = np.random.default_rng(7)


def make_train(n=60, lat0=43.24, lon0=76.89, ppsm=800_000.0) -> pd.DataFrame:
    """Кластер объявлений вокруг точки с заданной ₸/м²."""
    area = rng.uniform(40, 80, n)
    return pd.DataFrame({
        "price": area * ppsm,
        "area": area,
        "lat": lat0 + rng.normal(0, 0.002, n),  # ~200 м разброса
        "lon": lon0 + rng.normal(0, 0.002, n),
    })


def test_build_ref_and_hex_features():
    train = make_train()
    ref = build_spatial_ref(train)
    assert len(ref["ppsm"]) == len(train)
    assert ref["hex8"]  # кластер плотный — гекс res8 набрал min_n

    df = pd.DataFrame({"lat": [43.24], "lon": [76.89], "district_ppsm": [500_000.0]})
    out = add_spatial_features(df, ref=ref)
    assert 700_000 < out.loc[0, "hex8_ppsm"] < 900_000
    assert 700_000 < out.loc[0, "knn_ppsm"] < 900_000
    assert out.loc[0, "knn_n"] > 0


def test_fallback_to_district_ppsm_far_away():
    ref = build_spatial_ref(make_train())
    df = pd.DataFrame({"lat": [43.40], "lon": [77.10], "district_ppsm": [500_000.0]})
    out = add_spatial_features(df, ref=ref)
    # Далеко от кластера: нет гекса и соседей → фолбэк на district_ppsm
    assert out.loc[0, "hex7_ppsm"] == 500_000.0
    assert out.loc[0, "hex8_ppsm"] == 500_000.0
    assert out.loc[0, "knn_ppsm"] == 500_000.0
    assert out.loc[0, "knn_n"] < KNN_MIN_N


def test_no_ref_is_failsoft():
    df = pd.DataFrame({"lat": [43.24], "lon": [76.89], "district_ppsm": [500_000.0]})
    out = add_spatial_features(df, ref={"lat": [], "lon": [], "ppsm": [],
                                        "hex7": {}, "hex8": {}})
    assert out.loc[0, "knn_ppsm"] == 500_000.0


def test_knn_excludes_self():
    # Один дорогой выброс среди дешёвых соседей: без исключения «самого себя»
    # его собственная цена утекла бы в knn_ppsm
    train = make_train(n=30, ppsm=600_000.0)
    outlier = pd.DataFrame({
        "price": [50.0 * 2_000_000], "area": [50.0],
        "lat": [43.24], "lon": [76.89],
    })
    df = pd.concat([train, outlier], ignore_index=True)
    ref = build_spatial_ref(df)
    out = add_spatial_features(
        df.assign(district_ppsm=600_000.0), ref=ref,
        self_indices=self_indices_for(df),
    )
    knn_last = out["knn_ppsm"].iloc[-1]
    assert knn_last < 700_000  # медиана соседей, без собственных 2M ₸/м²


def test_self_indices_skip_rows_without_coords():
    df = pd.DataFrame({
        "price": [1e7, 1e7, 1e7], "area": [50, 50, 50],
        "lat": [43.24, None, 43.25], "lon": [76.89, None, 76.90],
    })
    idx = self_indices_for(df)
    assert list(idx) == [0, -1, 1]


def test_dedup_relistings():
    base = {"district": "X", "rooms": 2, "area": 55.0, "floor": 3,
            "total_floors": 9, "lat": 43.2401, "lon": 76.8901}
    df = pd.DataFrame([
        {"id": 1, "last_seen": "2026-06-01", **base},       # перезалито → уйдёт
        {"id": 2, "last_seen": "2026-06-10", **base},       # свежее — остаётся
        {"id": 3, "last_seen": "2026-06-05", **base, "area": 70.0},  # другая квартира
        {"id": 4, "last_seen": None, "district": "Y", "rooms": 1, "area": None,
         "floor": 1, "total_floors": 5, "lat": None, "lon": None},  # без отпечатка
    ])
    out = dedup_relistings(df)
    assert sorted(out["id"]) == [2, 3, 4]


def test_building_groups():
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "lat": [43.24011, 43.24012, 43.30],   # первые два — один дом (~1 м)
        "lon": [76.89011, 76.89012, 76.95],
    })
    g = building_groups(df)
    assert g.iloc[0] == g.iloc[1]
    assert g.iloc[0] != g.iloc[2]


def test_llm_flag_features_from_column():
    from krisha.features import add_llm_flag_features

    df = pd.DataFrame([
        {"id": 1, "llm_flags": ["bargain", "needs_repair"]},
        {"id": 2, "llm_flags": '["pledge"]'},
        {"id": 3, "llm_flags": None, "description": ""},
    ])
    out = add_llm_flag_features(df)
    assert out.loc[0, "flag_bargain"] == 1
    assert out.loc[0, "flag_needs_repair"] == 1
    assert out.loc[0, "flags_known"] == 1
    assert out.loc[1, "flag_pledge"] == 1
    assert out.loc[2, "flags_known"] == 0
    assert out.loc[2, "flag_bargain"] == 0
