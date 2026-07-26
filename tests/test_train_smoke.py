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


def test_purge_leaked_train_rows_keeps_other_units_in_same_building():
    """issue #104 доработка после ревью: purge по зданиям убрали — другая
    квартира того же дома в train легитимна (модель в проде всегда видит
    соседние лоты того же здания), это не утечка, только перевыставление
    (тот же fingerprint) — утечка."""
    df = synthetic_df(50)
    raw_test = df.iloc[:5].copy()
    other_unit = raw_test.iloc[[0]].copy()
    # Тот же дом (координаты совпадают на ~10 м), но другая квартира:
    # другие комнаты/площадь/этаж -> другой fingerprint.
    other_unit["rooms"] = other_unit["rooms"] + 1
    other_unit["area"] = other_unit["area"] + 15
    other_unit["floor"] = (other_unit["floor"] % 9) + 1
    raw_train = pd.concat([df.iloc[10:], other_unit], ignore_index=True)
    purged, n_purged = purge_leaked_train_rows(raw_train, raw_test)
    assert n_purged == 0
    assert len(purged) == len(raw_train)


def test_train_survives_train_part_narrower_than_calib_window(monkeypatch):
    """Регрессия: еженедельный retrain падал с CatBoostError «Labels variable
    is empty» и не отработал ни разу.

    time_based_split задаёт минимальный размер только для ПРАВОЙ (свежей)
    части — на левую нижней границы нет. В прод-базе first_seen укладывался
    в ~месяц с разрывом, после общего сплита train оказался разовым
    bulk-краулом за одни сутки, а окно калибровки в TEST_WINDOW_DAYS дней
    забрало все его строки: fit остался пустым. Гварды проверяли только
    правую часть (len(cal_idx) == 0 / len(test_idx) == 0), поэтому фолбэк на
    групповой сплит не срабатывал и пустой Pool уезжал в CatBoost.

    days_span=1 воспроизводит перекос в чистом виде: вся история короче
    окна, так что и общий сплит, и fit/calib обязаны уйти в фолбэк.
    """
    monkeypatch.setattr("krisha.zones.load_zone_index", lambda *a, **k: None)
    metrics = train(df=synthetic_df(n=300, days_span=1), iterations=60, save=False)

    assert metrics["n_train"] > 0
    assert metrics["n_test"] > 0
    assert metrics["model"]["mae"] > 0


def _days_df(spec):
    """spec: {смещение_дня_назад: сколько_строк} → фрейм с id и first_seen."""
    base = pd.Timestamp("2026-07-20")
    rows, next_id = [], 1
    for back, count in sorted(spec.items(), reverse=True):
        day = base - pd.Timedelta(days=back)
        for _ in range(count):
            rows.append({"id": next_id, "first_seen": day.isoformat(sep=" ")})
            next_id += 1
    return pd.DataFrame(rows)


def test_split_caps_test_share_and_keeps_bulk_day_in_train():
    """issue #153: у окна был только пол, без потолка — на реальной базе это
    дало test 61% при train 39%, причём весь train был разовым краулом за сутки."""
    from krisha.train import TEST_MAX_FRACTION, time_based_split

    # Профиль прода: гора первичного обхода + ровный дневной приток.
    df = _days_df({40: 5000, **{d: 750 for d in range(0, 14)}})
    train_idx, test_idx = time_based_split(df)

    n = len(df)
    assert len(test_idx) / n <= TEST_MAX_FRACTION + 0.001, "тест обязан влезать в потолок"
    assert len(train_idx) > len(test_idx), "train должен быть больше теста"

    ts_train = pd.to_datetime(df.iloc[train_idx]["first_seen"])
    bulk_day = pd.Timestamp("2026-07-20") - pd.Timedelta(days=40)
    assert (ts_train.dt.floor("D") == bulk_day).sum() == 5000, "вся гора — в train"
    ts_test = pd.to_datetime(df.iloc[test_idx]["first_seen"])
    assert (ts_test.dt.floor("D") == bulk_day).sum() == 0, "горы в тесте быть не должно"


def test_split_anchor_skips_a_bulk_day_that_is_the_freshest():
    """Ключевой случай: гора бэкфилла — САМЫЙ СВЕЖИЙ день, то есть попадает
    ровно в тестовое окно. Якорь обязан встать на день перед ней."""
    from krisha.train import time_based_split

    df = _days_df({0: 6000, **{d: 700 for d in range(1, 15)}})
    _, test_idx = time_based_split(df)

    ts_test = pd.to_datetime(df.iloc[test_idx]["first_seen"]).dt.floor("D")
    assert len(test_idx) > 0
    assert ts_test.max() == pd.Timestamp("2026-07-19"), "свежайший день теста — до горы"
    assert (ts_test == pd.Timestamp("2026-07-20")).sum() == 0


def test_bulk_detection_is_relative_and_off_on_short_history():
    """Детектор bulk-дней относительный (кратность медианы), поэтому:

    1. Ровный датасет из одинаково больших дней НЕ объявляется сплошным
       заливом — иначе после разгребания очереди, когда каждый день станет
       большим, сплит бы деградировал в групповой фолбэк.
    2. При истории короче BULK_MIN_DAYS_FOR_MEDIAN медиана бессмысленна, и
       детект отключается целиком — иначе на молодой базе первый же обычный
       день ложно уехал бы в train.
    """
    from krisha.train import TEST_MAX_FRACTION, time_based_split

    uniform = _days_df({d: 5000 for d in range(0, 8)})
    train_idx, test_idx = time_based_split(uniform)
    assert len(test_idx) > 0, "ровные большие дни — это не залив"
    assert len(test_idx) / len(uniform) <= TEST_MAX_FRACTION + 0.001

    short = _days_df({0: 5000, 10: 400, 20: 400})
    train_short, test_short = time_based_split(short)
    ts_test = pd.to_datetime(short.iloc[test_short]["first_seen"]).dt.floor("D")
    assert len(test_short) > 0
    assert (ts_test == pd.Timestamp("2026-07-20")).sum() > 0, "на короткой истории bulk не ищем"


def test_split_is_reproducible_when_a_day_does_not_fit_whole():
    """Последний день не влезает целиком — подвыборка должна быть
    детерминированной, иначе метрики скачут между запусками CI."""
    from krisha.train import time_based_split

    df = _days_df({0: 4000, 1: 400, 2: 400})
    first = time_based_split(df)
    second = time_based_split(df)
    assert np.array_equal(first[1], second[1])
    assert len(first[1]) > 0


def test_split_keeps_rows_without_first_seen_in_train():
    from krisha.train import time_based_split

    df = _days_df({d: 600 for d in range(0, 12)})
    df.loc[df.index[:50], "first_seen"] = None
    train_idx, test_idx = time_based_split(df)
    assert set(range(50)).issubset(set(train_idx.tolist())), "строки без даты — в train"
