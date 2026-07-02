"""Тесты прогноза цен по районам (линейный тренд недельных медиан)."""

from krisha import forecast
from krisha.forecast import _fit_forecast, build_forecast


def _trend(values):
    return [{"week": f"w{i}", "median_ppsm": v, "n": 100} for i, v in enumerate(values)]


def test_fit_forecast_too_few_points():
    assert _fit_forecast(_trend([500_000] * 5)) is None


def test_fit_forecast_linear_growth():
    # +2000 ₸/м² в неделю от 500к: через 13 недель ≈ 500к + 7*2к + 13*2к
    values = [500_000 + 2_000 * i for i in range(8)]
    fc = _fit_forecast(_trend(values))
    assert fc["current_ppsm"] == 514_000 and fc["weeks_used"] == 8
    assert abs(fc["m3"]["ppsm"] - 540_000) < 1_000
    assert abs(fc["m6"]["ppsm"] - 566_000) < 1_000
    assert fc["m3"]["change_pct"] > 0 and fc["m6"]["change_pct"] > fc["m3"]["change_pct"]

    flat = _fit_forecast(_trend([500_000] * 8))
    assert abs(flat["m6"]["change_pct"]) < 0.1


def test_build_forecast_shape(monkeypatch):
    def fake_trend(db_path, max_weeks=26, min_n=100, district=None):
        if district in (None, "Bostandykskiy_r-n"):
            return _trend([500_000 - 1_000 * i for i in range(10)])
        return []  # в остальных районах данных мало

    monkeypatch.setattr(forecast, "_weekly_trend", fake_trend)
    data = build_forecast(db_path="ignored.db")
    assert data["city"]["weeks_used"] == 10
    assert [d["district"] for d in data["districts"]] == ["Бостандыкский"]
    assert data["districts"][0]["m6"]["change_pct"] < 0
    assert "не инвестиционный совет" in data["disclaimer"]
