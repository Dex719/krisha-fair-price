"""Тесты KNN-цены соседей (Задача 2.1) и дедупа — фокус на анти-утечке."""

import numpy as np
import pandas as pd

from krisha.features import _fingerprint_series, build_features, dedup
from krisha.knn import (
    KNN_FEATURES,
    KnnPriceIndex,
    build_knn_index,
    load_knn_index,
    save_knn_index,
)


def _grid_df(n=60, seed=0):
    rng = np.random.default_rng(seed)
    lat = 43.20 + rng.uniform(0, 0.1, n)
    lon = 76.80 + rng.uniform(0, 0.1, n)
    area = rng.uniform(30, 100, n)
    # цена за м² растёт с широтой → у соседей похожая ppm2
    ppm2 = 400_000 + (lat - 43.20) * 5_000_000 + rng.normal(0, 5_000, n)
    return pd.DataFrame({
        "price": (ppm2 * area).round().astype(int),
        "area": area, "lat": lat, "lon": lon,
        "rooms": rng.integers(1, 4, n), "floor": rng.integers(1, 9, n),
        "total_floors": 9, "district": "Bostandykskiy_r-n",
    })


def test_index_query_basic():
    df = _grid_df()
    idx = build_knn_index(df, k=5)
    assert len(idx) == len(df)
    ppm2, dist = idx.query(df["lat"], df["lon"])
    assert ppm2.shape == (len(df),)
    assert np.isfinite(ppm2).all()
    assert (dist >= 0).all()


def test_self_neighbor_excludes_point():
    """knn_self=True не должен включать саму точку (анти-утечка на train)."""
    df = _grid_df()
    idx = build_knn_index(df, k=5)
    ppm2_self, _ = idx.query(df["lat"], df["lon"], self_neighbor=True)
    ppm2_incl, _ = idx.query(df["lat"], df["lon"], self_neighbor=False)
    # с самим собой медиана ближе к собственной ppm2, без — отличается
    own = df["price"] / df["area"]
    err_self = np.abs(ppm2_self - own).mean()
    err_incl = np.abs(ppm2_incl - own).mean()
    assert err_self > err_incl  # выкинули себя → дальше от собственной цены


def test_missing_coords_fallback():
    df = _grid_df()
    idx = build_knn_index(df, k=5)
    ppm2, dist = idx.query([np.nan], [np.nan])
    assert ppm2[0] == idx.global_ppm2  # fallback на глобальную медиану
    assert np.isnan(dist[0])


def test_empty_index_returns_nan():
    idx = KnnPriceIndex([], [], [], k=5)
    assert len(idx) == 0
    ppm2, dist = idx.query([43.2], [76.8])
    assert np.isnan(ppm2[0]) and np.isnan(dist[0])


def test_save_load_roundtrip(tmp_path):
    df = _grid_df()
    idx = build_knn_index(df, k=7)
    path = tmp_path / "knn_index.npz"
    save_knn_index(idx, path)
    loaded = load_knn_index(path)
    assert loaded is not None
    assert loaded.k == 7
    assert len(loaded) == len(idx)
    p1, _ = idx.query(df["lat"][:3], df["lon"][:3])
    p2, _ = loaded.query(df["lat"][:3], df["lon"][:3])
    assert np.allclose(p1, p2)


def test_load_missing_returns_none(tmp_path):
    assert load_knn_index(tmp_path / "nope.npz") is None


def test_build_features_adds_knn_columns():
    df = _grid_df(n=40)
    idx = build_knn_index(df, k=5)
    out = build_features(df, knn_index=idx, knn_self=True)
    for col in KNN_FEATURES:
        assert col in out.columns
    assert out["knn_ppm2"].notna().all()


def test_build_features_no_index_nan_fallback():
    df = _grid_df(n=10)
    out = build_features(df, knn_index=None, knn_self=False)
    # без снапшота на диске knn-фичи становятся NaN, но колонки есть
    for col in KNN_FEATURES:
        assert col in out.columns


def test_dedup_collapses_reposts():
    df = _grid_df(n=30)
    twin = df.iloc[[0]].copy()  # перезалив того же объекта (те же гео-поля)
    twin["price"] = int(df.iloc[0]["price"] * 1.2)  # другая цена — всё равно дубль
    dup_df = pd.concat([df, twin], ignore_index=True)
    out = dedup(dup_df)
    assert len(out) == len(df)  # один дубль схлопнут


def test_dedup_keeps_distinct():
    df = _grid_df(n=30)
    assert len(dedup(df)) == len(df)


def test_fingerprint_none_without_coords():
    df = pd.DataFrame({"area": [50], "lat": [np.nan], "lon": [76.8],
                       "district": ["x"], "rooms": [2], "floor": [3], "total_floors": [9]})
    assert _fingerprint_series(df).iloc[0] is None
