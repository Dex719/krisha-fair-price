"""Смоук-тест обучения: синтетика, мало итераций, без сохранения на диск."""

import sqlite3

import numpy as np
import pandas as pd

from krisha.db import init_db, upsert_listing
from krisha.train import (
    load_dataset,
    purge_leaked_train_rows,
    time_based_split,
    train,
)

rng = np.random.default_rng(42)


def synthetic_df(n=400, days_span=90):
    area = rng.uniform(30, 120, n)
    rooms = np.clip((area / 30).astype(int), 1, 4)
    district = rng.choice(["Bostandykskiy_r-n", "Alatauskiy_r-n", "Medeuskiy_r-n"], n)
    ppsm = np.where(district == "Medeuskiy_r-n", 900_000, 550_000) + rng.normal(0, 30_000, n)
    first_seen = pd.Timestamp("2026-01-01") + pd.to_timedelta(
        rng.integers(0, days_span, n), unit="D"
    )
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
        "first_seen": first_seen.astype(str),
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
    # issue #104: сплит теперь временной + purge — total сохраняется как
    # train + test + purged (purge выкидывает строки из train, не в test).
    assert metrics["n_train"] + metrics["n_test"] + metrics["n_purged"] == 400
    assert metrics["n_test"] > 0
    assert "time_based" in metrics["split"]


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


def test_load_dataset_filters_stale_delisted_and_out_of_area(tmp_path):
    """issue #104: is_active=0 давно снятые и координаты вне Алматы — не в train."""
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(
        {"id": 1, "url": "https://krisha.kz/a/show/1", "price": 40_000_000, "area": 60.0,
         "lat": 43.24, "lon": 76.89},
        db,
    )
    upsert_listing(
        {"id": 2, "url": "https://krisha.kz/a/show/2", "price": 40_000_000, "area": 60.0,
         "lat": 51.16, "lon": 71.47},  # Астана — другой город
        db,
    )
    upsert_listing(
        {"id": 3, "url": "https://krisha.kz/a/show/3", "price": 40_000_000, "area": 60.0,
         "lat": 43.20, "lon": 76.90},  # активный Алматы — должен остаться
        db,
    )
    with sqlite3.connect(db) as conn:
        # Лот 1 снят с продажи 200 дней назад — устарел, должен уйти из train
        conn.execute(
            "UPDATE listings SET is_active = 0, delisted_at = ? WHERE id = 1",
            (str(pd.Timestamp.now(tz="utc") - pd.Timedelta(days=200)),),
        )
        conn.commit()

    df = load_dataset(db)

    assert set(df["id"]) == {3}


def test_time_based_split_orders_by_first_seen():
    df = synthetic_df(200)
    train_idx, test_idx = time_based_split(df, window_days=14, min_fraction=0.1)
    assert len(test_idx) > 0
    ts = pd.to_datetime(df["first_seen"])
    assert ts.iloc[train_idx].max() <= ts.iloc[test_idx].min() or len(train_idx) == 0


def test_time_based_split_no_first_seen_returns_empty_test():
    df = synthetic_df(50).drop(columns=["first_seen"])
    train_idx, test_idx = time_based_split(df)
    assert len(test_idx) == 0
    assert len(train_idx) == 50


def test_purge_leaked_train_rows_removes_matching_fingerprint():
    df = synthetic_df(50)
    raw_test = df.iloc[:5].copy()
    # Дублируем одну test-строку в train с тем же отпечатком (перевыставление)
    raw_train = pd.concat([df.iloc[10:], raw_test.iloc[[0]]], ignore_index=True)
    purged, n_purged = purge_leaked_train_rows(raw_train, raw_test)
    assert n_purged >= 1
    assert len(purged) == len(raw_train) - n_purged


def test_purge_leaked_train_rows_noop_on_empty_test():
    df = synthetic_df(20)
    purged, n_purged = purge_leaked_train_rows(df, df.iloc[0:0])
    assert n_purged == 0
    assert len(purged) == len(df)
