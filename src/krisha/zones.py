"""Зоны Алматы по координатам: район, микрорайон, «общий пин».

Проблема: krisha отдаёт district/microdistrict из своей карты зон — микрорайон
пуст у ~57% объявлений, район иногда не совпадает с координатами, а ~15%
объявлений висят на одной точке (метка ЖК). Здесь эти поля чинятся по
снапшоту models/osm_zones.json (см. scripts/fetch_osm_zones.py):

- район — точка-в-полигоне по границам OSM (admin_level=6);
- микрорайон — полигоны OSM, привязанные к меткам krisha, иначе ближайший
  центроид метки по базе (с ограничением расстояния);
- «общий пин» — координата, где сидит много объявлений → бейдж
  «координаты примерные» в карточке.

Всё fail-soft: нет снапшота → функции возвращают None и ничего не меняют.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from krisha.config import OSM_ZONES_SNAPSHOT_PATH

SHARED_PIN_MIN = 5        # объявлений на одной точке → это метка ЖК, не дом
MICRO_CENTROID_MAX_KM = 1.2  # дальше центроида метку не присваиваем
MICRO_CENTROID_MIN_N = 3     # центроид по <3 объявлениям не используем
_ROUND = 5                # округление координат при сверке с shared_pins


def point_in_ring(lat: float, lon: float, ring: list[list[float]]) -> bool:
    """Чётно-нечётный тест (ray casting) для кольца [[lat, lon], ...]."""
    inside = False
    n = len(ring)
    for i in range(n - 1):
        y1, x1 = ring[i]
        y2, x2 = ring[i + 1]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if x_cross > lon:
                inside = not inside
    return inside


def point_in_polygon(lat: float, lon: float, polygon: dict) -> bool:
    """Полигон {"outer": [кольца], "inner": [кольца-дырки]}."""
    if not any(point_in_ring(lat, lon, ring) for ring in polygon.get("outer", [])):
        return False
    return not any(point_in_ring(lat, lon, ring) for ring in polygon.get("inner", []))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


class ZoneIndex:
    """Индекс зон из снапшота osm_zones.json."""

    def __init__(self, snapshot: dict):
        self.districts: list[dict] = snapshot.get("districts", [])
        self.microdistricts: list[dict] = snapshot.get("microdistricts", [])
        # label -> (lat, lon); центроиды по слишком малому числу точек шумят
        self.centroids: dict[str, tuple[float, float]] = {
            label: (v[0], v[1])
            for label, v in (snapshot.get("micro_centroids") or {}).items()
            if len(v) < 3 or v[2] >= MICRO_CENTROID_MIN_N
        }
        self.shared_pins: dict[tuple[float, float], int] = {
            (p[0], p[1]): int(p[2]) for p in snapshot.get("shared_pins", [])
        }
        # Быстрый bbox-претест колец, чтобы не гонять ray casting по всем районам
        self._district_bboxes = [self._bbox(d["polygon"]) for d in self.districts]
        self._micro_bboxes = [self._bbox(m["polygon"]) for m in self.microdistricts]

    @staticmethod
    def _bbox(polygon: dict) -> tuple[float, float, float, float]:
        pts = [p for ring in polygon.get("outer", []) for p in ring]
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        return min(lats), min(lons), max(lats), max(lons)

    def district(self, lat: float, lon: float) -> str | None:
        """Ключ района krisha (Almalinskiy_r-n, ...) по координатам, иначе None."""
        if lat is None or lon is None or math.isnan(lat) or math.isnan(lon):
            return None
        for d, bb in zip(self.districts, self._district_bboxes):
            if bb[0] <= lat <= bb[2] and bb[1] <= lon <= bb[3] and \
                    point_in_polygon(lat, lon, d["polygon"]):
                return d["key"]
        return None

    def microdistrict(self, lat: float, lon: float) -> str | None:
        """Метка микрорайона krisha (mkr_Aksay-1, ...) по координатам, иначе None."""
        if lat is None or lon is None or math.isnan(lat) or math.isnan(lon):
            return None
        for m, bb in zip(self.microdistricts, self._micro_bboxes):
            if bb[0] <= lat <= bb[2] and bb[1] <= lon <= bb[3] and \
                    point_in_polygon(lat, lon, m["polygon"]):
                return m["label"]
        best, best_km = None, MICRO_CENTROID_MAX_KM
        for label, (clat, clon) in self.centroids.items():
            km = _haversine_km(lat, lon, clat, clon)
            if km <= best_km:
                best, best_km = label, km
        return best

    def shared_pin_count(self, lat: float, lon: float) -> int:
        """Сколько объявлений сидит ровно на этой точке (0 — точка уникальна)."""
        if lat is None or lon is None:
            return 0
        try:
            key = (round(float(lat), _ROUND), round(float(lon), _ROUND))
        except (TypeError, ValueError):
            return 0
        return self.shared_pins.get(key, 0)


@lru_cache(maxsize=1)
def load_zone_index(path: Path | str | None = None) -> ZoneIndex | None:
    """Снапшот зон → индекс. Нет файла → None (всё работает как раньше)."""
    path = Path(path or OSM_ZONES_SNAPSHOT_PATH)
    if not path.exists():
        return None
    return ZoneIndex(json.loads(path.read_text(encoding="utf-8")))


def resolve_zones(df: pd.DataFrame, index: ZoneIndex | None = None) -> pd.DataFrame:
    """Чинит district/microdistrict по координатам + флаг district_mismatch.

    - district: пропуск → значение по полигону OSM; расхождение krisha и OSM →
      доверяем координатам (полигоны OSM точнее карты зон krisha) и ставим
      district_mismatch=1;
    - microdistrict: только заполняем пропуски (57% в базе), явные метки krisha
      не трогаем.
    """
    df = df.copy()
    if index is None:
        index = load_zone_index()
    for col in ("district", "microdistrict"):
        if col not in df:
            df[col] = None
    if index is None:
        df["district_mismatch"] = 0
        return df

    nan_series = pd.Series(np.nan, index=df.index)
    lat = pd.to_numeric(df["lat"], errors="coerce") if "lat" in df else nan_series
    lon = pd.to_numeric(df["lon"], errors="coerce") if "lon" in df else nan_series
    osm_district = [index.district(la, lo) for la, lo in zip(lat, lon)]

    current = df["district"].where(df["district"].notna(), None)
    mismatch, fixed = [], []
    for cur, osm in zip(current, osm_district):
        cur = cur if isinstance(cur, str) and cur else None
        if osm is None:
            mismatch.append(0)
            fixed.append(cur)
        elif cur is None:
            mismatch.append(0)
            fixed.append(osm)
        else:
            mismatch.append(int(cur != osm))
            fixed.append(osm if cur != osm else cur)
    df["district"] = fixed
    df["district_mismatch"] = mismatch

    micro = df["microdistrict"].where(df["microdistrict"].notna(), None)
    df["microdistrict"] = [
        m if isinstance(m, str) and m else index.microdistrict(la, lo)
        for m, la, lo in zip(micro, lat, lon)
    ]
    return df


def approximate_pin_note(lat: float | None, lon: float | None) -> dict[str, str] | None:
    """Элемент блока «Локация», если точка на карте — общая метка ЖК."""
    index = load_zone_index()
    if index is None or lat is None or lon is None:
        return None
    n = index.shared_pin_count(lat, lon)
    if n < SHARED_PIN_MIN:
        return None
    return {
        "label": "⚠️ Координаты примерные",
        "value": f"на этой точке {n} объявлений (метка ЖК)",
    }
