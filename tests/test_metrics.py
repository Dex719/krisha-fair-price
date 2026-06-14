"""Юнит-тесты честных метрик evaluate(): MdAPE и доля прогнозов в ±10%."""

import numpy as np

from krisha.train import evaluate


def test_evaluate_has_honest_metrics():
    y_true = np.array([100.0, 200.0, 300.0, 400.0])
    y_pred = np.array([100.0, 200.0, 300.0, 400.0])
    m = evaluate(y_true, y_pred)
    for key in ("mae", "mape", "mdape", "within_10pct", "r2"):
        assert key in m
    # Идеальный прогноз: ошибки нет, все в пределах ±10%.
    assert m["mdape"] == 0.0
    assert m["within_10pct"] == 1.0


def test_mdape_is_median_not_mean():
    # Один большой выброс не должен утягивать MdAPE так же, как MAPE.
    y_true = np.array([100.0, 100.0, 100.0, 100.0])
    y_pred = np.array([100.0, 100.0, 100.0, 200.0])  # 3 точных, 1 ошибка 100%
    m = evaluate(y_true, y_pred)
    assert m["mdape"] == 0.0                  # медиана ошибок = 0
    assert abs(m["mape"] - 0.25) < 1e-9       # среднее = 25%
    assert m["within_10pct"] == 0.75          # 3 из 4 в пределах ±10%


def test_within_10pct_boundary_inclusive():
    # Ровно 10% ошибки считается попаданием в ±10%.
    y_true = np.array([100.0, 100.0])
    y_pred = np.array([110.0, 91.0])          # +10% (внутри), -9% (внутри)
    m = evaluate(y_true, y_pred)
    assert m["within_10pct"] == 1.0
