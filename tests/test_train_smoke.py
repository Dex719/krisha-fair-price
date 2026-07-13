"""Смоук-тест обучения: синтетика, мало итераций, без сохранения на диск."""

import sqlite3

import numpy as np
import pandas as pd

from krisha.db import init_db, upsert_listing
from krisha.train import load_dataset, train

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


def test_train_pipeline_runs(monkeypatch):
    # На синтетике baseline почти идеален по построению, поэтому проверяем
    # только что пайплайн работает и модель адекватна (не что она бьёт baseline).
    # Районы здесь случайные — реальная зонная карта OSM их бы «починила»
    # по координатам и убила синтетический сигнал, поэтому отключаем её.
    monkeypatch.setattr("krisha.zones.load_zone_index", lambda *a, **k: None)
    metrics = train(df=synthetic_df(), iterations=100, save=False)
    assert metrics["model"]["r2"] > 0.5
    assert metrics["model"]["mape"] < 0.2
    assert metrics["baseline"]["mae"] > 0
    assert metrics["n_train"] + metrics["n_test"] == 400


def test_load_dataset_excludes_user_predicts(tmp_path):
    """issue #117 (доп.): source="user" — не источник истины для train, лоты,
    добавленные через predict_from_url, не должны попадать в датасет."""
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(
        {"id": 1, "url": "https://krisha.kz/a/show/1", "price": 40_000_000, "area": 60.0},
        db,
    )
    upsert_listing(
        {
            "id": 2,
            "url": "https://krisha.kz/a/show/2",
            "price": 41_000_000,
            "area": 61.0,
            "source": "user",
        },
        db,
    )

    df = load_dataset(db)

    assert set(df["id"]) == {1}


def test_load_dataset_handles_missing_source_column(tmp_path):
    """Старая БД без колонки source (до миграции) не должна ронять load_dataset."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE listings (id INTEGER PRIMARY KEY, price INTEGER, area REAL)")
    conn.execute("INSERT INTO listings VALUES (1, 40000000, 60.0)")
    conn.commit()
    conn.close()

    df = load_dataset(db)

    assert list(df["id"]) == [1]
