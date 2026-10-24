"""Аудит 24.10: стенд режет выборку так же, как прод (train.load_dataset +
dedup_relistings до сплита) — иначе стенд и прод мерили разные выборки."""

import sys
from pathlib import Path

import pandas as pd
from test_backtest import _synthetic_listing_row, _write_listing, _write_price_point

from krisha.config import STALE_DELISTED_DAYS
from krisha.db import get_conn, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backtest as bt  # noqa: E402

BASE = pd.Timestamp("2026-01-01", tz="utc")


def _fold_data(tmp_path, monkeypatch, extra):
    """База: 120 обычных лотов за 4 недели + extra(conn) — особые лоты."""
    monkeypatch.setattr("krisha.zones.load_zone_index", lambda *a, **k: None)
    db = tmp_path / "align.db"
    init_db(db)
    with get_conn(db) as conn:
        for i in range(1, 121):
            fs = BASE + pd.Timedelta(days=i % 28)
            price = 40_000_000 + i * 10_000
            _write_listing(conn, _synthetic_listing_row(i, fs, price))
            _write_price_point(conn, i, price, fs)
        extra(conn)
    listings, price_history = bt.load_raw_with_history(db)
    fold = bt.make_folds(BASE + pd.Timedelta(days=28), n_folds=1, window_days=7)[0]
    return fold, *bt.build_fold_data(listings, price_history, fold)


def _add(conn, lid, fs, *, price=41_000_000, **overrides):
    row = {**_synthetic_listing_row(lid, fs, price), **overrides}
    _write_listing(conn, row)
    _write_price_point(conn, lid, price, fs)


def test_fold_drops_out_of_area_lots(tmp_path, monkeypatch):
    def extra(conn):
        _add(conn, 900, BASE + pd.Timedelta(days=2), lat=51.13, lon=71.43)   # Астана → train
        _add(conn, 901, BASE + pd.Timedelta(days=25), lat=51.13, lon=71.43)  # Астана → test

    _, train_raw, test_raw, _ = _fold_data(tmp_path, monkeypatch, extra)
    assert 900 not in set(train_raw["id"]) and 901 not in set(test_raw["id"])
    assert len(train_raw) > 50 and len(test_raw) > 10


def test_fold_drops_lots_delisted_long_before_fold_but_keeps_later_delisted(tmp_path, monkeypatch):
    def extra(conn):
        _add(conn, 910, BASE - pd.Timedelta(days=STALE_DELISTED_DAYS + 60))
        _add(conn, 911, BASE + pd.Timedelta(days=1))
        stale = (BASE - pd.Timedelta(days=STALE_DELISTED_DAYS + 30)).strftime("%Y-%m-%d %H:%M:%S")
        later = (BASE + pd.Timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE listings SET is_active = 0, delisted_at = ? WHERE id = 910", (stale,))
        # снят ПОСЛЕ фолда — на дату фолда был живым, в train остаётся
        conn.execute("UPDATE listings SET is_active = 0, delisted_at = ? WHERE id = 911", (later,))

    _, train_raw, _, _ = _fold_data(tmp_path, monkeypatch, extra)
    ids = set(train_raw["id"])
    assert 910 not in ids
    assert 911 in ids


def test_fold_test_is_deduplicated_like_prod(tmp_path, monkeypatch):
    # одна квартира перевыставлена внутри тестовой недели под двумя id —
    # прод (dedup_relistings до сплита) считает её один раз
    twin = {"lat": 43.2001, "lon": 76.9001, "floor": 7, "total_floors": 12, "area": 61.0}

    def extra(conn):
        _add(conn, 920, BASE + pd.Timedelta(days=23), price=55_000_000, **twin)
        _add(conn, 921, BASE + pd.Timedelta(days=26), price=55_200_000, **twin)

    _, _, test_raw, _ = _fold_data(tmp_path, monkeypatch, extra)
    assert len({920, 921} & set(test_raw["id"])) == 1
