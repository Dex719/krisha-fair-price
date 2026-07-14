"""Доверительный интервал цены: вердикт по интервалу + конформное покрытие."""

import numpy as np
import pandas as pd

from krisha.interval import finalize_interval
from krisha.predict import _verdict_interval
from krisha.train import train

_rng = np.random.default_rng(7)


def synthetic_df(n=600, days_span=120):
    area = _rng.uniform(30, 120, n)
    rooms = np.clip((area / 30).astype(int), 1, 4)
    district = _rng.choice(["Bostandykskiy_r-n", "Alatauskiy_r-n", "Medeuskiy_r-n"], n)
    ppsm = np.where(district == "Medeuskiy_r-n", 900_000, 550_000) + _rng.normal(0, 40_000, n)
    first_seen = pd.Timestamp("2026-01-01") + pd.to_timedelta(
        _rng.integers(0, days_span, n), unit="D"
    )
    return pd.DataFrame({
        "price": (area * ppsm).astype(int),
        "area": area,
        "rooms": rooms,
        "district": district,
        "floor": _rng.integers(1, 10, n),
        "total_floors": 10,
        "year_built": _rng.integers(1970, 2025, n),
        "lat": 43.24 + _rng.normal(0, 0.03, n),
        "lon": 76.89 + _rng.normal(0, 0.03, n),
        "photos_count": _rng.integers(1, 15, n),
        "first_seen": first_seen.astype(str),
    })


def test_verdict_interval_outside_below_is_good_deal():
    assert _verdict_interval(40_000_000, 45_000_000, 55_000_000) == "GOOD_DEAL"


def test_verdict_interval_outside_above_is_overpriced():
    assert _verdict_interval(60_000_000, 45_000_000, 55_000_000) == "OVERPRICED"


def test_verdict_interval_inside_is_fair():
    # внутри интервала — никакого категоричного ярлыка (убираем «вердикт-в-шуме»)
    assert _verdict_interval(50_000_000, 45_000_000, 55_000_000) == "FAIR"
    assert _verdict_interval(45_000_000, 45_000_000, 55_000_000) == "FAIR"  # на границе
    assert _verdict_interval(55_000_000, 45_000_000, 55_000_000) == "FAIR"


def test_interval_meta_and_coverage(monkeypatch):
    """train() возвращает интервал, покрытие близко к целевому, границы корректны."""
    monkeypatch.setattr("krisha.zones.load_zone_index", lambda *a, **k: None)
    out = train(df=synthetic_df(600), iterations=150, save=False)
    interval = out["interval"]
    assert interval["cqr_scale"] >= 0
    assert interval["median_width_pct"] > 0
    # CQR даёт конечно-выборочную гарантию покрытия ~target (допускаем разброс на синтетике)
    assert interval["coverage_test"] >= interval["target_coverage"] - 0.15


def test_finalize_interval_swaps_crossed_bounds():
    lo, hi = finalize_interval(50.0, 60.0, 40.0)  # перепутаны местами
    assert lo == 40.0 and hi == 60.0


def test_finalize_interval_stretches_to_include_point():
    lo, hi = finalize_interval(70.0, 40.0, 60.0)  # точка выше hi
    assert lo == 40.0 and hi == 70.0
    lo, hi = finalize_interval(10.0, 40.0, 60.0)  # точка ниже lo
    assert lo == 10.0 and hi == 60.0


def test_finalize_interval_arrays():
    point = np.array([10.0, 50.0])
    lo, hi = finalize_interval(point, np.array([60.0, 40.0]), np.array([40.0, 60.0]))
    assert list(lo) == [10.0, 40.0]
    assert list(hi) == [60.0, 60.0]
