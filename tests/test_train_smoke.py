"""Смоук-тест обучения: синтетика, мало итераций, без сохранения на диск."""

import numpy as np
import pandas as pd

from krisha.train import train

rng = np.random.default_rng(42)


def synthetic_df(n=400):
    area = rng.uniform(30, 120, n)
    rooms = np.clip((area / 30).astype(int), 1, 4)
    district = rng.choice(["Bostandykskiy_r-n", "Alatauskiy_r-n", "Medeuskiy_r-n"], n)
    ppsm = np.where(district == "Medeuskiy_r-n", 900_000, 550_000) + rng.normal(0, 30_000, n)
    return pd.DataFrame({
        "price": (area * ppsm).astype(int),
        "area": area,
        "rooms": rooms,
        "district": district,
        "floor": rng.integers(1, 10, n),
        "total_floors": 10,
        "year_built": rng.integers(1970, 2025, n),
        "lat": 43.24 + rng.normal(0, 0.03, n),
        "lon": 76.89 + rng.normal(0, 0.03, n),
        "photos_count": rng.integers(1, 15, n),
    })


def test_train_pipeline_runs():
    # На синтетике baseline почти идеален по построению, поэтому проверяем
    # только что пайплайн работает и модель адекватна (не что она бьёт baseline).
    metrics = train(df=synthetic_df(), iterations=100, save=False)
    assert metrics["model"]["r2"] > 0.5
    assert metrics["model"]["mape"] < 0.2
    assert metrics["baseline"]["mae"] > 0
    assert metrics["n_train"] + metrics["n_test"] == 400
