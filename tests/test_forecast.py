"""Тесты прогноза цен по районам (линейный тренд недельных медиан)."""

from krisha import forecast
from krisha.forecast import _fit_forecast, build_forecast


def _trend(values, gaps: set[int] | None = None):
    """Синтетический тренд. `gaps` — множество индексов недель (0-based, по
    порядку значений), которые считаются пропущенными в календаре: `t`
    прыгает через них, как это делает реальный `_weekly_trend`, когда
    `_fit_forecast` не должен путать соседние точки списка со соседними
    неделями."""
    gaps = gaps or set()
    out = []
    t = 0
    for i, v in enumerate(values):
        if i in gaps:
            t += 1  # пропущенная неделя — t прыгает, точка не добавляется
        out.append({"week": f"w{i}", "t": t, "median_ppsm": v, "n": 100})
        t += 1
    return out


def test_fit_forecast_too_few_points():
    assert _fit_forecast(_trend([500_000] * 5)) is None


def test_fit_forecast_linear_growth():
    # +2000 ₸/м² в неделю от 500к, 12 точек (>= MIN_WEEKS_M6) — m3 и m6 оба видны.
    values = [500_000 + 2_000 * i for i in range(12)]
    fc = _fit_forecast(_trend(values))
    assert fc["current_ppsm"] == 522_000 and fc["weeks_used"] == 12
    assert abs(fc["m3"]["ppsm"] - 548_000) < 1_000
    assert abs(fc["m6"]["ppsm"] - 574_000) < 1_000
    assert fc["m3"]["change_pct"] > 0 and fc["m6"]["change_pct"] > fc["m3"]["change_pct"]
    assert fc["slope_significant"] is True

    # Все недельные медианы совпадают: вырожденный случай (нулевая дисперсия
    # остатков), обрабатывается детерминированно — иначе float-шум в
    # np.polyfit(..., cov=True) может дать наклон/ошибку одного порядка и
    # случайно "значимый" результат при нулевом реальном тренде.
    flat = _fit_forecast(_trend([500_000] * 12))
    assert abs(flat["m6"]["change_pct"]) < 0.1
    assert flat["slope_significant"] is False

    # Реалистичный слабый шум без направленного тренда — тоже не значим.
    noisy_flat = [500_000, 500_200, 499_800, 500_100, 499_900, 500_300,
                  499_700, 500_000, 500_200, 499_800, 500_100, 499_900]
    fc_noisy_flat = _fit_forecast(_trend(noisy_flat))
    assert fc_noisy_flat["slope_significant"] is False


def test_fit_forecast_hides_m6_below_min_weeks():
    # 8 недель истории (< MIN_WEEKS_M6=12): m3 остаётся, m6 прячем — мало данных
    # для полугодового горизонта, но выше MIN_POINTS=6, так что прогноз в целом есть.
    values = [500_000 + 2_000 * i for i in range(8)]
    fc = _fit_forecast(_trend(values))
    assert fc is not None
    assert "m3" in fc
    assert "m6" not in fc


def test_fit_forecast_calendar_gap_not_treated_as_adjacent():
    """issue #109: раньше X = np.arange(len(y)) — пропущенная неделя (min_n
    не набрался и _weekly_trend её не добавила через `continue`) склеивала
    соседние по списку точки, как будто они соседние по календарю недели,
    и наклон/экстраполяция считались по сжатой шкале времени.

    Здесь неделя с индексом 5 "пропущена" в календаре (t перепрыгивает через
    неё), хотя в списке trend соседние точки идут подряд. Наклон на реальную
    календарную неделю должен быть меньше наивного (по индексу списка),
    потому что то же изменение цены растянуто на на одну календарную неделю
    больше, чем показывает голый порядковый номер точки.
    """
    values = [500_000 + 5_000 * i for i in range(12)]
    trend_with_gap = _trend(values, gaps={5})
    trend_no_gap = _trend(values)

    fc_gap = _fit_forecast(trend_with_gap)
    fc_flat_index = _fit_forecast(
        [{"week": t["week"], "t": i, "median_ppsm": t["median_ppsm"], "n": t["n"]}
         for i, t in enumerate(trend_with_gap)]
    )
    # То же самое разбитое на пропуск множество точек, но по индексу
    # (старое поведение) даёт другой (более крутой) наклон, чем по
    # реальному календарному `t` — подтверждает, что фикс что-то меняет.
    assert fc_gap["slope_week_pct"] != fc_flat_index["slope_week_pct"]
    assert fc_gap["slope_week_pct"] < fc_flat_index["slope_week_pct"]

    fc_no_gap = _fit_forecast(trend_no_gap)
    fc_no_gap_by_index = _fit_forecast(
        [{"week": t["week"], "t": i, "median_ppsm": t["median_ppsm"], "n": t["n"]}
         for i, t in enumerate(trend_no_gap)]
    )
    # Без пропуска календарный и наивный (по индексу) расчёт совпадают (t == index).
    assert fc_no_gap["slope_week_pct"] == fc_no_gap_by_index["slope_week_pct"]


def test_build_forecast_shape(monkeypatch):
    def fake_trend(db_path, max_weeks=26, min_n=100, district=None):
        if district in (None, "Bostandykskiy_r-n"):
            return _trend([500_000 - 1_000 * i for i in range(12)])
        return []  # в остальных районах данных мало

    monkeypatch.setattr(forecast, "_weekly_trend", fake_trend)
    data = build_forecast(db_path="ignored.db")
    assert data["city"]["weeks_used"] == 12
    assert [d["district"] for d in data["districts"]] == ["Бостандыкский"]
    assert data["districts"][0]["m6"]["change_pct"] < 0
    assert "не инвестиционный совет" in data["disclaimer"]
