"""Тесты scripts/backtest.py (issue #130): walk-forward стенд честной оценки."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from krisha.db import get_conn, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backtest as bt  # noqa: E402

rng = np.random.default_rng(7)


# --- make_folds --------------------------------------------------------

def test_make_folds_boundaries_and_order():
    max_ts = pd.Timestamp("2026-04-01", tz="utc")
    folds = bt.make_folds(max_ts, n_folds=4, window_days=7)
    assert len(folds) == 4
    # Самый свежий фолд (последний) — test = последняя неделя данных
    last = folds[-1]
    assert last.test_end == max_ts
    assert last.test_start == max_ts - pd.Timedelta(days=7)
    assert last.calib_start == max_ts - pd.Timedelta(days=14)
    # Фолды идут строго по возрастанию времени, без пропусков/перекрытий
    for prev, nxt in zip(folds, folds[1:]):
        assert prev.test_end == nxt.test_start
        assert prev.test_start == nxt.calib_start


# --- asof_prices ---------------------------------------------------------

def test_asof_prices_picks_last_point_strictly_before_cutoff():
    ph = pd.DataFrame({
        "listing_id": [1, 1, 1, 2],
        "price": [100, 110, 120, 500],
        "observed_at": pd.to_datetime([
            "2026-01-01", "2026-01-10", "2026-01-20", "2026-01-05",
        ], utc=True),
    })
    cutoff = pd.Timestamp("2026-01-15", tz="utc")
    prices = bt.asof_prices(ph, cutoff)
    assert prices[1] == 110  # точка 01-20 после cutoff — не должна попасть
    assert prices[2] == 500


def test_asof_prices_exact_timestamp_not_included():
    """Строго ДО даты (allow_exact_matches=False у merge_asof-эквивалента) —
    точка ровно на cutoff не должна попасть в реконструкцию (иначе train
    видел бы цену, ставшую известной именно в момент среза, не раньше)."""
    ph = pd.DataFrame({
        "listing_id": [1],
        "price": [999],
        "observed_at": pd.to_datetime(["2026-01-15"], utc=True),
    })
    cutoff = pd.Timestamp("2026-01-15", tz="utc")
    prices = bt.asof_prices(ph, cutoff)
    assert 1 not in prices.index


# --- build_fold_data: без утечки из будущего -------------------------------

def _synthetic_listing_row(i, first_seen, price, district="Bostandykskiy_r-n"):
    return dict(
        id=i, url=f"https://krisha.kz/a/show/{i}", title="t", price=price,
        rooms=2, area=50.0, floor=3, total_floors=10, building_type="monolit",
        year_built=2010, ceiling=2.7, district=district, microdistrict=None,
        street=None, house_num=None, address_title=None, complex_name=None,
        lat=43.24 + i * 0.0001, lon=76.89 + i * 0.0001, user_type="owner",
        category="vtorichka", description=None, photos_count=5, raw_params=None,
        source="scrape", first_seen=str(first_seen),
    )


def _write_listing(conn, row):
    cols = [c for c in row if c != "first_seen"] + ["fingerprint", "first_seen", "last_seen", "is_active"]
    payload = {**row, "fingerprint": None, "last_seen": row["first_seen"], "is_active": 1}
    placeholders = ", ".join(f":{c}" for c in cols)
    conn.execute(
        f"INSERT INTO listings ({', '.join(cols)}) VALUES ({placeholders})", payload,
    )


def _write_price_point(conn, listing_id, price, observed_at):
    conn.execute(
        "INSERT INTO price_history (listing_id, price, observed_at) VALUES (?, ?, ?)",
        (listing_id, price, str(observed_at)),
    )


def test_build_fold_data_train_price_ignores_future_change(tmp_path):
    """Лот появился давно (в train), цена честно изменилась ВНУТРИ недели
    теста фолда — train не должен видеть эту новую цену (это будущее
    относительно fold.test_start)."""
    db = tmp_path / "t.db"
    init_db(db)
    with get_conn(db) as conn:
        # много старых лотов, чтобы train/calib прошли минимальный размер
        base = pd.Timestamp("2026-01-01", tz="utc")
        for i in range(1, 60):
            fs = base + pd.Timedelta(days=int(rng.integers(0, 20)))
            price = 40_000_000 + int(rng.integers(-2_000_000, 2_000_000))
            _write_listing(conn, _synthetic_listing_row(i, fs, price))
            _write_price_point(conn, i, price, fs)
        # калибровочная неделя (первая неделя после первых 20 дней)
        for i in range(60, 75):
            fs = base + pd.Timedelta(days=int(20 + rng.integers(0, 7)))
            price = 40_000_000
            _write_listing(conn, _synthetic_listing_row(i, fs, price))
            _write_price_point(conn, i, price, fs)
        for i in range(75, 90):
            fs = base + pd.Timedelta(days=int(27 + rng.integers(0, 7)))
            price = 40_000_000
            _write_listing(conn, _synthetic_listing_row(i, fs, price))
            _write_price_point(conn, i, price, fs)
        # лот #1 (в train) меняет цену в разгар недели теста — будущее для фолда
        conn.execute(
            "UPDATE listings SET price = ? WHERE id = 1", (999_000_000,),
        )
        _write_price_point(conn, 1, 999_000_000, base + pd.Timedelta(days=29))

    listings, price_history = bt.load_raw_with_history(db)
    max_ts = listings["first_seen"].max()
    folds = bt.make_folds(max_ts, n_folds=1, window_days=7)
    fold = folds[0]
    train_raw, calib_raw, test_raw, _ = bt.build_fold_data(listings, price_history, fold)

    row1 = train_raw[train_raw["id"] == 1]
    if len(row1):  # дедуп/purge теоретически могли его выкинуть — если остался, цена честная
        assert row1["price"].iloc[0] != 999_000_000
        assert row1["price"].iloc[0] < 100_000_000


def test_build_fold_data_respects_first_seen_boundaries(tmp_path):
    db = tmp_path / "t2.db"
    init_db(db)
    base = pd.Timestamp("2026-01-01", tz="utc")
    with get_conn(db) as conn:
        for i in range(1, 200):
            fs = base + pd.Timedelta(days=int(rng.integers(0, 28)))
            price = 40_000_000
            _write_listing(conn, _synthetic_listing_row(i, fs, price))
            _write_price_point(conn, i, price, fs)

    listings, price_history = bt.load_raw_with_history(db)
    max_ts = listings["first_seen"].max()
    folds = bt.make_folds(max_ts, n_folds=1, window_days=7)
    fold = folds[0]
    train_raw, calib_raw, test_raw, _ = bt.build_fold_data(listings, price_history, fold)

    if len(train_raw):
        assert (train_raw["first_seen"] < fold.calib_start).all()
    if len(calib_raw):
        assert (
            (calib_raw["first_seen"] >= fold.calib_start) & (calib_raw["first_seen"] < fold.test_start)
        ).all()
    if len(test_raw):
        assert (
            (test_raw["first_seen"] >= fold.test_start) & (test_raw["first_seen"] < fold.test_end)
        ).all()


# --- сквозной прогон на синтетике -----------------------------------------

def _synthetic_db(path, n=1200, weeks=12):
    init_db(path)
    base = pd.Timestamp("2026-01-01", tz="utc")
    with get_conn(path) as conn:
        for i in range(1, n + 1):
            area = float(rng.uniform(30, 120))
            district = rng.choice(["Bostandykskiy_r-n", "Alatauskiy_r-n", "Medeuskiy_r-n"])
            ppsm = (900_000 if district == "Medeuskiy_r-n" else 550_000) + rng.normal(0, 20_000)
            price = int(area * ppsm)
            fs = base + pd.Timedelta(days=int(rng.integers(0, weeks * 7)))
            row = _synthetic_listing_row(i, fs, price, district=district)
            row["area"] = area
            row["rooms"] = int(np.clip(area // 30, 1, 4))
            _write_listing(conn, row)
            _write_price_point(conn, i, price, fs)


def test_run_backtest_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("krisha.zones.load_zone_index", lambda *a, **k: None)
    db = tmp_path / "big.db"
    _synthetic_db(db)
    combined, report = bt.run_backtest(
        db_path=db, n_folds=3, window_days=7, point_iterations=40, quantile_iterations=40,
    )
    assert report["n_folds_run"] >= 1
    o = report["overall"]
    assert o["n"] == len(combined)
    assert 0 <= o["coverage"] <= 1
    assert o["mape"] > 0
    assert np.isfinite(o["pinball_q10"])
    assert np.isfinite(o["pinball_q90"])
    # per-row csv должен содержать всё нужное для paired-сравнения
    for col in ("fold", "listing_id", "price_true", "price_pred", "price_lo", "price_hi"):
        assert col in combined.columns


def test_run_fold_returns_none_when_too_small():
    empty = pd.DataFrame()
    assert bt.run_fold(empty, empty, empty) is None


# --- compare_runs ----------------------------------------------------------

def test_compare_runs_pairs_by_fold_and_listing(tmp_path):
    a = pd.DataFrame({
        "fold": [0, 0, 1],
        "listing_id": [1, 2, 3],
        "price_true": [100.0, 200.0, 300.0],
        "price_pred": [110.0, 190.0, 330.0],
        "price_lo": [90.0, 170.0, 270.0],
        "price_hi": [130.0, 210.0, 350.0],
    })
    b = a.copy()
    b["price_pred"] = [100.0, 200.0, 300.0]  # версия B — идеальные предикты

    csv_a, csv_b = tmp_path / "a_predictions.csv", tmp_path / "b_predictions.csv"
    a.to_csv(csv_a, index=False)
    b.to_csv(csv_b, index=False)

    report_md = bt.compare_runs(csv_a, csv_b, "a", "b")
    assert "Спаренных строк: 3" in report_md
    assert "MAPE" in report_md


def test_compare_runs_raises_on_no_overlap(tmp_path):
    a = pd.DataFrame({
        "fold": [0], "listing_id": [1], "price_true": [100.0], "price_pred": [100.0],
        "price_lo": [90.0], "price_hi": [110.0],
    })
    b = pd.DataFrame({
        "fold": [0], "listing_id": [999], "price_true": [100.0], "price_pred": [100.0],
        "price_lo": [90.0], "price_hi": [110.0],
    })
    csv_a, csv_b = tmp_path / "a_predictions.csv", tmp_path / "b_predictions.csv"
    a.to_csv(csv_a, index=False)
    b.to_csv(csv_b, index=False)
    with pytest.raises(ValueError):
        bt.compare_runs(csv_a, csv_b, "a", "b")
