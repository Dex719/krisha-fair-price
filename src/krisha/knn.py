"""KNN-цена соседей — главная геофича (Задача 2.1 ТЗ).

Для каждой квартиры берём K ближайших по координатам объявлений и считаем
медианную цену за м². Это локальный прайс-сигнал тоньше, чем медиана по
району/микрорайону: ловит разницу цен между соседними кварталами и ЖК.

⚠️ Утечка таргета — главный риск задания. Поэтому:
- Дерево соседей строится ТОЛЬКО на train-части (в predict — на сохранённом
  снапшоте train-объявлений из models/knn_index.npz).
- Для train-строк первый сосед — сама точка, его выкидываем (`self_neighbor=True`).
- Перед постройкой индекса датасет дедуплицируется по геоотпечатку
  (см. features.dedup): перезалив одного объявления = «сосед» с той же ценой,
  иначе модель списывает ответ у самой себя и даёт фейково низкий MAPE.
"""

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

EARTH_R_KM = 6371.0
KNN_K = 5  # сколько соседей усредняем (подобрано по holdout: k=5 даёт лучший MAPE/R²)
KNN_FEATURES = ["knn_ppm2", "knn_dist_km"]


class KnnPriceIndex:
    """BallTree (haversine) по координатам объявлений + их цена за м².

    Хранит только валидные точки (есть lat/lon и положительная ppm2).
    `query` возвращает медианную ₸/м² соседей и среднее расстояние до них (км).
    """

    def __init__(self, lat, lon, ppm2, k: int = KNN_K):
        lat = np.asarray(pd.to_numeric(pd.Series(lat), errors="coerce"), dtype=float)
        lon = np.asarray(pd.to_numeric(pd.Series(lon), errors="coerce"), dtype=float)
        ppm2 = np.asarray(pd.to_numeric(pd.Series(ppm2), errors="coerce"), dtype=float)
        ok = np.isfinite(lat) & np.isfinite(lon) & np.isfinite(ppm2) & (ppm2 > 0)
        self.lat = lat[ok]
        self.lon = lon[ok]
        self.ppm2 = ppm2[ok]
        self.k = int(k)
        self.global_ppm2 = float(np.median(self.ppm2)) if self.ppm2.size else float("nan")
        self._tree = None
        if self.ppm2.size:
            from sklearn.neighbors import BallTree

            coords = np.radians(np.column_stack([self.lat, self.lon]))
            self._tree = BallTree(coords, metric="haversine")

    def __len__(self) -> int:
        return int(self.ppm2.size)

    def query(self, lat, lon, self_neighbor: bool = False):
        """Медианная ₸/м² K соседей и среднее расстояние до них (км).

        self_neighbor=True — точки сами лежат в индексе (train): берём k+1
        соседей и выкидываем первого (самого себя). Нет координат / пустой
        индекс → fallback: глобальная медиана ₸/м², расстояние NaN.
        """
        lat = np.asarray(pd.to_numeric(pd.Series(lat), errors="coerce"), dtype=float)
        lon = np.asarray(pd.to_numeric(pd.Series(lon), errors="coerce"), dtype=float)
        n = len(lat)
        knn_ppm2 = np.full(n, self.global_ppm2, dtype=float)
        knn_dist = np.full(n, np.nan, dtype=float)
        if self._tree is None or self.ppm2.size == 0:
            return knn_ppm2, knn_dist
        ok = np.isfinite(lat) & np.isfinite(lon)
        if not ok.any():
            return knn_ppm2, knn_dist
        kq = self.k + (1 if self_neighbor else 0)
        kq = min(kq, self.ppm2.size)
        pts = np.radians(np.column_stack([lat[ok], lon[ok]]))
        dist, idx = self._tree.query(pts, k=kq)
        if self_neighbor and kq > 1:
            dist, idx = dist[:, 1:], idx[:, 1:]
        knn_ppm2[ok] = np.median(self.ppm2[idx], axis=1)
        knn_dist[ok] = dist.mean(axis=1) * EARTH_R_KM
        return knn_ppm2, knn_dist


def build_knn_index(df: pd.DataFrame, k: int = KNN_K) -> KnnPriceIndex:
    """Индекс соседей из train-датафрейма (нужны колонки price, area, lat, lon)."""
    area = pd.to_numeric(df.get("area"), errors="coerce")
    price = pd.to_numeric(df.get("price"), errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        ppm2 = price / area
    return KnnPriceIndex(df.get("lat"), df.get("lon"), ppm2, k=k)


def save_knn_index(index: KnnPriceIndex, path: Path | str) -> None:
    """Снапшот индекса (lat/lon/ppm2/k) для инференса без БД — деплою на Railway."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path, lat=index.lat, lon=index.lon, ppm2=index.ppm2, k=np.int64(index.k)
    )


def load_knn_index(path: Path | str) -> KnnPriceIndex | None:
    """Загрузка снапшота индекса. Нет файла → None (фичи будут NaN-fallback)."""
    path = Path(path)
    if not path.exists():
        return None
    data = np.load(path)
    return KnnPriceIndex(data["lat"], data["lon"], data["ppm2"], k=int(data["k"]))


@lru_cache(maxsize=1)
def load_default_knn_index() -> KnnPriceIndex | None:
    """Кэшированный индекс из models/knn_index.npz (для predict)."""
    from krisha.config import KNN_INDEX_PATH

    return load_knn_index(KNN_INDEX_PATH)
