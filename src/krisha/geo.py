"""Этап 3 роадмапа: локация по OpenStreetMap.

POI Алматы (метро, школы, детсады, парки, супермаркеты, остановки, крупные
дороги, промзоны) лежат снапшотом в models/osm_pois.json — собирается разово
скриптом scripts/fetch_osm_pois.py через Overpass API.

Здесь: загрузка снапшота, быстрый поиск ближайших точек (KD-дерево на
локальной равноугольной проекции) и фичи расстояний для модели.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from krisha.config import OSM_POIS_SNAPSHOT_PATH

# Категория POI → имя фичи расстояния (км до ближайшей точки)
POI_DISTANCE_FEATURES = {
    "metro": "dist_metro_km",
    "school": "dist_school_km",
    "kindergarten": "dist_kindergarten_km",
    "park": "dist_park_km",
    "supermarket": "dist_supermarket_km",
    "bus_stop": "dist_bus_stop_km",
    "big_road": "dist_big_road_km",      # анти-фактор: магистраль под окнами
    "industrial": "dist_industrial_km",  # анти-фактор: промзона рядом
    # Строящееся метро (продление до «Калкаман», дек. 2026; трасса до «Барлык»,
    # ~2030): гипотеза «капитализация до запуска» НЕ подтвердилась на парном
    # walk-forward AB (авг 2026): в сегменте <1.5 км (n=2116) MAPE 7.53→7.57,
    # Wilcoxon p=0.36 — эффект уже сидит в hex7/hex8_ppsm. POI-категория
    # metro_construction остаётся в models/osm_pois.json (см. fetch_osm_pois.py)
    # для factor_hints/фронта; в фичи расстояние не подаём.
}
GEO_FEATURES = [*POI_DISTANCE_FEATURES.values(), "walk_score"]

# Категории «пешей доступности» для walk_score и подсчёта вокруг точки
_WALK_CATS = ("metro", "school", "kindergarten", "park", "supermarket", "bus_stop")
# Дальше этого расстояния (км) категория не даёт вклада в walk_score
_WALK_FULL_KM = {
    "metro": 1.0, "school": 0.7, "kindergarten": 0.7,
    "park": 0.8, "supermarket": 0.5, "bus_stop": 0.5,
}

_LAT0 = math.radians(43.25)  # широта Алматы для проекции
_KM_PER_DEG = 111.32


class PoiIndex:
    """KD-деревья по категориям POI. Координаты — (lat, lon)."""

    def __init__(self, pois: dict[str, list[list[float]]]):
        from scipy.spatial import cKDTree

        self.trees: dict[str, object] = {}
        for cat, points in pois.items():
            arr = np.asarray(points, dtype=float)
            if arr.size == 0:
                continue
            self.trees[cat] = cKDTree(self._project(arr[:, 0], arr[:, 1]))

    @staticmethod
    def _project(lat, lon) -> np.ndarray:
        """Равноугольная проекция в км — для малой области точность отличная."""
        x = np.asarray(lon) * _KM_PER_DEG * math.cos(_LAT0)
        y = np.asarray(lat) * _KM_PER_DEG
        return np.column_stack([x, y])

    def nearest_km(self, cat: str, lat, lon) -> np.ndarray:
        """Расстояние (км) до ближайшей точки категории; нет данных → NaN."""
        lat = np.atleast_1d(np.asarray(lat, dtype=float))
        lon = np.atleast_1d(np.asarray(lon, dtype=float))
        out = np.full(len(lat), np.nan)
        tree = self.trees.get(cat)
        ok = ~(np.isnan(lat) | np.isnan(lon))
        if tree is None or not ok.any():
            return out
        dist, _ = tree.query(self._project(lat[ok], lon[ok]))
        out[ok] = dist
        return out


@lru_cache(maxsize=1)
def load_poi_index(path: Path | str = OSM_POIS_SNAPSHOT_PATH) -> PoiIndex | None:
    """Снапшот POI → индекс. Нет файла → None (фичи будут NaN)."""
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return PoiIndex(data.get("pois", data))


def walk_score(dists: dict[str, float]) -> float:
    """Скор пешей доступности 0–100: вклад категории линейно затухает к порогу."""
    score = 0.0
    known = 0
    for cat in _WALK_CATS:
        d = dists.get(cat)
        if d is None or (isinstance(d, float) and math.isnan(d)):
            continue
        known += 1
        # квадратичное затухание: близко к POI ≈ полный балл, у порога → 0
        score += max(0.0, 1.0 - (d / _WALK_FULL_KM[cat]) ** 2)
    if known == 0:
        # «Нет данных» — это NaN, а не 0.0. Ноль означает «всё далеко», то есть
        # худшую пешую доступность в городе: лот без координат (или прогон без
        # снапшота OSM) выглядел для модели как окраина без единого POI, вместо
        # честного пропуска, который CatBoost умеет обрабатывать нативно.
        return float("nan")
    return round(score / len(_WALK_CATS) * 100, 1)


def add_geo_features(df: pd.DataFrame, index: PoiIndex | None = None) -> pd.DataFrame:
    """Фичи расстояний до POI + walk_score. Нет координат/снапшота → NaN."""
    df = df.copy()
    if index is None:
        index = load_poi_index()
    lat = pd.to_numeric(df.get("lat"), errors="coerce")
    lon = pd.to_numeric(df.get("lon"), errors="coerce")
    dist_by_cat = {}
    for cat, col in POI_DISTANCE_FEATURES.items():
        if col in df and df[col].notna().all():
            dist_by_cat[cat] = pd.to_numeric(df[col], errors="coerce").to_numpy()
            continue
        vals = index.nearest_km(cat, lat, lon) if index is not None else np.full(len(df), np.nan)
        df[col] = vals
        dist_by_cat[cat] = vals
    df["walk_score"] = [
        walk_score({cat: dist_by_cat[cat][i] for cat in _WALK_CATS})
        for i in range(len(df))
    ]
    return df


# (подпись, категория) — блок «Локация» в карточке оценки
_LOCATION_RU = [
    ("Метро", "metro"),
    ("Школа", "school"),
    ("Детсад", "kindergarten"),
    ("Парк", "park"),
    ("Супермаркет", "supermarket"),
    ("Остановка", "bus_stop"),
]
BIG_ROAD_WARN_KM = 0.15   # магистраль ближе → предупреждение
INDUSTRIAL_WARN_KM = 0.4  # промзона ближе → предупреждение


def _fmt_km(d: float) -> str:
    return f"{int(round(d * 1000, -1))} м" if d < 1 else f"{d:.1f} км"


def build_location_details(lat: float | None, lon: float | None) -> list[dict[str, str]]:
    """Блок «Локация»: walk_score, ближайшие POI и предупреждения. [] если нет данных."""
    index = load_poi_index()
    if index is None or lat is None or lon is None:
        return []
    dists = {cat: float(index.nearest_km(cat, lat, lon)[0]) for cat in POI_DISTANCE_FEATURES}
    items = [{"label": "Пешая доступность", "value": f"{walk_score(dists):g} / 100"}]
    items += [
        {"label": label, "value": _fmt_km(dists[cat])}
        for label, cat in _LOCATION_RU
        if not math.isnan(dists.get(cat, float("nan")))
    ]
    road = dists.get("big_road")
    if road is not None and not math.isnan(road) and road <= BIG_ROAD_WARN_KM:
        items.append({"label": "⚠️ Магистраль рядом", "value": _fmt_km(road)})
    ind = dists.get("industrial")
    if ind is not None and not math.isnan(ind) and ind <= INDUSTRIAL_WARN_KM:
        items.append({"label": "⚠️ Промзона рядом", "value": _fmt_km(ind)})
    return items
