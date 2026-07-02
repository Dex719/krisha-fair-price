"""Пространственные фичи цены: H3-гексагоны и соседи (KNN spatial lag).

Идея: district/microdistrict — слишком крупные зоны, цена меняется от
квартала к кварталу. Считаем на train-части (без утечки в метрики):

- медианную ₸/м² по гексагонам H3 res 7 (~5 км²) и res 8 (~0.7 км²)
  с фолбэком fine → coarse → district_ppsm;
- KNN spatial lag: медианная ₸/м² у K ближайших объявлений в радиусе 1 км
  (на train сосед-«сам» исключается) + число таких соседей.

Референс сохраняется в models/spatial_ref.json при обучении и используется
в predict. Нет файла → фичи падают в фолбэк (NaN/district_ppsm), всё fail-soft.
"""

import json
import logging
import math
from functools import lru_cache
from pathlib import Path

import h3
import numpy as np
import pandas as pd

from krisha.config import SPATIAL_REF_PATH

logger = logging.getLogger(__name__)

HEX_RES_COARSE = 7   # ~5.2 км² на гекс
HEX_RES_FINE = 8     # ~0.74 км² на гекс
HEX_MIN_N = 8        # медиана по меньшему числу объявлений слишком шумная
KNN_K = 15           # соседей для spatial lag
KNN_MAX_KM = 1.0     # дальше — не сосед
KNN_MIN_N = 3        # меньше соседей → NaN (фолбэк на гекс/район)

SPATIAL_FEATURES = ["hex7_ppsm", "hex8_ppsm", "knn_ppsm", "knn_n"]

_LAT0 = math.radians(43.25)  # широта Алматы, локальная проекция как в geo.py
_KM_PER_DEG = 111.32


def _project(lat, lon) -> np.ndarray:
    x = np.asarray(lon, dtype=float) * _KM_PER_DEG * math.cos(_LAT0)
    y = np.asarray(lat, dtype=float) * _KM_PER_DEG
    return np.column_stack([x, y])


def _hex_map(lat, lon, ppsm, res: int) -> dict[str, float]:
    cells = [
        h3.latlng_to_cell(la, lo, res)
        for la, lo in zip(lat, lon)
    ]
    df = pd.DataFrame({"cell": cells, "ppsm": ppsm})
    grp = df.groupby("cell")["ppsm"]
    stats = grp.agg(["median", "count"])
    return {c: float(m) for c, (m, n) in stats.iterrows() if n >= HEX_MIN_N}


def spatial_ref_mask(df: pd.DataFrame) -> pd.Series:
    """Строки, попадающие в референс (есть цена, площадь и координаты)."""
    return df[["price", "area", "lat", "lon"]].notna().all(axis=1) & (df["area"] > 0)


def self_indices_for(df: pd.DataFrame) -> np.ndarray:
    """Позиция каждой строки train-df в референсе, -1 — строки нет в ref.

    Нужно, чтобы на train исключать «самого себя» из KNN-соседей.
    """
    mask = spatial_ref_mask(df).to_numpy()
    out = np.full(len(df), -1)
    out[mask] = np.arange(int(mask.sum()))
    return out


def build_spatial_ref(train_df: pd.DataFrame) -> dict:
    """Референс по train-части: точки с ₸/м² и медианы по гексагонам."""
    sub = train_df[spatial_ref_mask(train_df)].copy()
    lat = sub["lat"].astype(float).to_numpy()
    lon = sub["lon"].astype(float).to_numpy()
    ppsm = (sub["price"] / sub["area"]).astype(float).to_numpy()
    ref = {
        "lat": [round(v, 6) for v in lat],
        "lon": [round(v, 6) for v in lon],
        "ppsm": [round(v, 1) for v in ppsm],
        "hex7": _hex_map(lat, lon, ppsm, HEX_RES_COARSE),
        "hex8": _hex_map(lat, lon, ppsm, HEX_RES_FINE),
    }
    logger.info(
        "spatial ref: %d точек, %d гексов res7, %d гексов res8",
        len(ppsm), len(ref["hex7"]), len(ref["hex8"]),
    )
    return ref


def save_spatial_ref(ref: dict, path: Path | str = SPATIAL_REF_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(ref, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


@lru_cache(maxsize=1)
def load_spatial_ref(path: Path | str | None = None) -> dict | None:
    """Референс из models/spatial_ref.json; нет файла → None (фичи в фолбэк)."""
    p = Path(path or SPATIAL_REF_PATH)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _knn_ppsm(
    lat: np.ndarray,
    lon: np.ndarray,
    ref: dict,
    self_indices: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Медианная ₸/м² соседей в радиусе KNN_MAX_KM и их число.

    self_indices — позиция каждой строки в ref (train-режим): сам себе
    объект не сосед, иначе получилась бы утечка таргета.
    """
    n = len(lat)
    knn = np.full(n, np.nan)
    knn_n = np.zeros(n)
    ref_lat = np.asarray(ref["lat"], dtype=float)
    if ref_lat.size == 0:
        return knn, knn_n
    from scipy.spatial import cKDTree

    ref_ppsm = np.asarray(ref["ppsm"], dtype=float)
    tree = cKDTree(_project(ref_lat, np.asarray(ref["lon"], dtype=float)))
    ok = ~(np.isnan(lat) | np.isnan(lon))
    if not ok.any():
        return knn, knn_n
    k = min(KNN_K + 1, ref_lat.size)
    dist, idx = tree.query(_project(lat[ok], lon[ok]), k=k,
                           distance_upper_bound=KNN_MAX_KM)
    dist = np.atleast_2d(dist)
    idx = np.atleast_2d(idx)
    rows = np.where(ok)[0]
    for r, (d_row, i_row) in zip(rows, zip(dist, idx)):
        valid = np.isfinite(d_row)
        neigh = i_row[valid]
        if self_indices is not None and self_indices[r] >= 0:
            neigh = neigh[neigh != self_indices[r]]
        neigh = neigh[:KNN_K]
        if len(neigh) >= KNN_MIN_N:
            knn[r] = float(np.median(ref_ppsm[neigh]))
        knn_n[r] = len(neigh)
    return knn, knn_n


def add_spatial_features(
    df: pd.DataFrame,
    ref: dict | None = None,
    self_indices: np.ndarray | None = None,
) -> pd.DataFrame:
    """Фичи hex7_ppsm/hex8_ppsm/knn_ppsm/knn_n. Фолбэк — district_ppsm.

    Вызывается после расчёта district_ppsm в build_features.
    """
    df = df.copy()
    if ref is None:
        ref = load_spatial_ref()
    fallback = df["district_ppsm"] if "district_ppsm" in df else pd.Series(np.nan, index=df.index)
    if ref is None:
        for col in SPATIAL_FEATURES:
            df[col] = fallback if col.endswith("_ppsm") else 0.0
        return df

    lat = pd.to_numeric(df.get("lat"), errors="coerce").to_numpy() \
        if "lat" in df else np.full(len(df), np.nan)
    lon = pd.to_numeric(df.get("lon"), errors="coerce").to_numpy() \
        if "lon" in df else np.full(len(df), np.nan)

    hex7 = np.full(len(df), np.nan)
    hex8 = np.full(len(df), np.nan)
    for i, (la, lo) in enumerate(zip(lat, lon)):
        if np.isnan(la) or np.isnan(lo):
            continue
        hex7[i] = ref["hex7"].get(h3.latlng_to_cell(la, lo, HEX_RES_COARSE), np.nan)
        hex8[i] = ref["hex8"].get(h3.latlng_to_cell(la, lo, HEX_RES_FINE), np.nan)

    knn, knn_n = _knn_ppsm(lat, lon, ref, self_indices=self_indices)

    fb = fallback.to_numpy(dtype=float)
    df["hex7_ppsm"] = np.where(np.isnan(hex7), fb, hex7)
    df["hex8_ppsm"] = np.where(np.isnan(hex8), df["hex7_ppsm"], hex8)
    df["knn_ppsm"] = np.where(np.isnan(knn), df["hex8_ppsm"], knn)
    df["knn_n"] = knn_n
    return df
