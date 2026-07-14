"""Тесты krisha.targets (issue #131): режимы таргета + city index + вес свежести."""

import numpy as np
import pandas as pd
import pytest

from krisha import targets as tg


def _basket_rows(n_weeks=6, per_week=10, ppsm_start=500_000, weekly_drift=20_000):
    """Синтетика: одна hex-ячейка, растущая ₸/м² по неделям (линейный дрейф)."""
    base = pd.Timestamp("2026-01-05", tz="utc")  # понедельник
    rows = []
    for w in range(n_weeks):
        ppsm = ppsm_start + w * weekly_drift
        for i in range(per_week):
            area = 50.0
            price = ppsm * area
            rows.append({
                "price": price, "area": area,
                "lat": 43.222, "lon": 76.851,  # одна и та же точка -> одна hex-ячейка
                "first_seen": base + pd.Timedelta(weeks=w, days=i % 3),
            })
    return pd.DataFrame(rows)


def test_build_city_index_tracks_weekly_median():
    df = _basket_rows()
    ref = tg.build_city_index(df, min_cell_n=5)
    assert ref["basket_cells"] == 1
    weeks = sorted(ref["weekly"])
    assert len(weeks) == 6
    # растёт монотонно вместе с синтетическим дрейфом
    values = [ref["weekly"][w] for w in weeks]
    assert values == sorted(values)
    assert values[0] == pytest.approx(500_000, rel=1e-6)
    assert values[-1] == pytest.approx(600_000, rel=1e-6)


def test_index_at_falls_back_to_last_known_week_for_future_ts():
    df = _basket_rows()
    ref = tg.build_city_index(df, min_cell_n=5)
    last_week_val = ref["weekly"][max(ref["weekly"])]
    future_ts = pd.Timestamp("2027-06-01", tz="utc")
    assert tg.index_at(ref, future_ts) == pytest.approx(last_week_val)
    assert tg.index_now(ref) == pytest.approx(last_week_val)


def test_index_at_and_now_fallback_to_global_when_no_basket():
    df = _basket_rows(n_weeks=1, per_week=3)  # мало строк всего -> пустая корзина
    ref = tg.build_city_index(df, min_cell_n=8)
    assert ref["basket_cells"] == 0
    assert tg.index_at(ref, pd.Timestamp("2026-02-01", tz="utc")) == pytest.approx(ref["global"])
    assert tg.index_now(ref) == pytest.approx(ref["global"])


def test_add_target_column_and_predict_price_roundtrip_price_mode():
    df = pd.DataFrame({"price": [40_000_000.0], "area": [50.0], "first_seen": [pd.Timestamp("2026-01-01", tz="utc")]})
    df["log_price"] = np.log1p(df["price"])
    out, col = tg.add_target_column(df, "price")
    assert col == "log_price"
    price = tg.predict_price(out[col].to_numpy(), out["area"], "price")
    assert price[0] == pytest.approx(40_000_000.0, rel=1e-6)


def test_add_target_column_and_predict_price_roundtrip_ppsm_mode():
    df = pd.DataFrame({"price": [40_000_000.0], "area": [50.0], "first_seen": [pd.Timestamp("2026-01-01", tz="utc")]})
    out, col = tg.add_target_column(df, "ppsm")
    assert col == "log_ppsm"
    price = tg.predict_price(out[col].to_numpy(), out["area"], "ppsm")
    assert price[0] == pytest.approx(40_000_000.0, rel=1e-6)


def test_add_target_column_and_predict_price_roundtrip_index_residual_mode():
    train = _basket_rows()
    ref = tg.build_city_index(train, min_cell_n=5)
    row = pd.DataFrame({
        "price": [55_000_000.0], "area": [100.0],
        "first_seen": [pd.Timestamp("2026-02-10", tz="utc")],
    })
    out, col = tg.add_target_column(row, "index_residual", index_ref=ref)
    assert col == "log_ppsm_resid"
    price = tg.predict_price(out[col].to_numpy(), out["area"], "index_residual", index_ref=ref)
    # предикт использует ЕДИНЫЙ index_now (последняя неделя train), а не
    # неделю самой строки -> обратный переход воспроизводит цену, только
    # если index_at(строка) == index_now (иначе умышленно другое число).
    if tg.index_at(ref, row["first_seen"].iloc[0]) == tg.index_now(ref):
        assert price[0] == pytest.approx(55_000_000.0, rel=1e-6)
    else:
        assert price[0] > 0


def test_add_target_column_index_residual_requires_index_ref():
    df = pd.DataFrame({"price": [1.0], "area": [1.0], "first_seen": [pd.Timestamp.now(tz="utc")]})
    with pytest.raises(ValueError):
        tg.add_target_column(df, "index_residual", index_ref=None)


def test_freshness_weight_half_life_decay():
    as_of = pd.Timestamp("2026-06-01", tz="utc")
    fs = pd.Series([as_of, as_of - pd.Timedelta(days=90), as_of - pd.Timedelta(days=180)])
    w = tg.freshness_weight(fs, as_of, half_life_days=90)
    assert w[0] == pytest.approx(1.0)
    assert w[1] == pytest.approx(0.5, rel=1e-6)
    assert w[2] == pytest.approx(0.25, rel=1e-6)


def test_freshness_weight_clips_negative_age_to_one():
    as_of = pd.Timestamp("2026-06-01", tz="utc")
    fs = pd.Series([as_of + pd.Timedelta(days=5)])  # "из будущего" — не должно случаться, но не должно давать вес > 1
    w = tg.freshness_weight(fs, as_of, half_life_days=90)
    assert w[0] == pytest.approx(1.0)


def test_target_col_and_modes_consistency():
    assert set(tg.TARGET_MODES) == set(tg.TARGET_COL_BY_MODE)
    with pytest.raises(ValueError):
        tg.target_col("bogus")
