import numpy as np
import pytest
from catboost import CatBoostRegressor


def test_apply_cqr_new_format_normalized_scale():
    """Новый формат меты: cqr_scale масштабирует по ширине сырого интервала."""
    from krisha.predict import _apply_cqr

    # lo_raw=1.0, hi_raw=2.0 -> width=1.0, scale=0.2 -> lo-0.2, hi+0.2
    log_lo, log_hi = _apply_cqr(1.0, 2.0, {"cqr_scale": 0.2})
    assert log_lo == 0.8
    assert log_hi == 2.2


def test_apply_cqr_old_format_fallback_offset():
    """Обратная совместимость: у прод-модели до retrain в meta есть только
    старый cqr_offset_log (issue #105 доработка после ревью) — должна
    применяться старая формула с фиксированным сдвигом, а не scale=0."""
    from krisha.predict import _apply_cqr

    log_lo, log_hi = _apply_cqr(1.0, 2.0, {"cqr_offset_log": 0.3})
    assert log_lo == 1.0 - 0.3
    assert log_hi == 2.0 + 0.3


def test_apply_cqr_prefers_new_key_when_both_present():
    from krisha.predict import _apply_cqr

    log_lo, log_hi = _apply_cqr(1.0, 2.0, {"cqr_scale": 0.2, "cqr_offset_log": 0.3})
    assert log_lo == 0.8  # новый формат побеждает
    assert log_hi == 2.2


def test_apply_cqr_missing_meta_is_noop():
    """Ни cqr_scale, ни cqr_offset_log нет — интервал не расширяется (offset=0)."""
    from krisha.predict import _apply_cqr

    log_lo, log_hi = _apply_cqr(1.0, 2.0, {})
    assert log_lo == 1.0
    assert log_hi == 2.0


def test_apply_cqr_clips_to_safe_range():
    from krisha.predict import _apply_cqr

    log_lo, log_hi = _apply_cqr(0.0, 100.0, {"cqr_offset_log": 1000.0})
    assert log_lo == -30.0
    assert log_hi == 30.0


def test_with_money_impact_log_space_conversion():
    """SHAP-вклад из log-пространства переводится в % и тенге корректно."""
    import numpy as np

    from krisha.predict import _with_money_impact

    fair = 50_000_000.0
    base = 40_000_000.0
    factors = [{"feature": "area", "impact": 0.2}, {"feature": "floor", "impact": -0.1}]
    out = _with_money_impact(factors, fair, base_price=base)
    assert out[0]["impact_pct"] == round((np.expm1(0.2)) * 100, 1)  # +22.1%
    # тенге на единицу вклада — логарифмическое среднее (1+fair) и (1+base)
    scale = (fair - base) / (np.log1p(fair) - np.log1p(base))
    assert out[0]["impact_tenge"] == round(0.2 * scale, -4)
    assert out[1]["impact_pct"] < 0 and out[1]["impact_tenge"] < 0


def test_money_impacts_add_up_to_estimate_minus_base():
    """База + вклады всех факторов = оценка (в тенге, а не только в логах).

    Старая формула fair·(1 − e^{−s}) на реальных лотах расходилась с
    fair − base на 10–30%: «база + вклады» не давали оценку."""
    from krisha.predict import _money_per_log_unit

    base_log = 17.59
    shap = np.array([0.6, -0.16, -0.07, 0.05, 0.03, -0.02])
    fair = float(np.expm1(base_log + shap.sum()))
    base = float(np.expm1(base_log))

    tenge = shap * _money_per_log_unit(fair, base)

    assert tenge.sum() == pytest.approx(fair - base, rel=1e-9)
    # знак и порядок вкладов сохраняются
    assert list(np.sign(tenge)) == list(np.sign(shap))
    # без вкладов (fair == base) — без деления на ноль
    assert _money_per_log_unit(base, base) == pytest.approx(base + 1)


def test_explain_price_merges_collinear_features_before_top_n():
    """Год постройки и возраст дома — один фактор «Возраст дома», вклад суммой,
    и он не отнимает место у пятого настоящего фактора."""
    from krisha.predict import explain_price

    features = ["year_built", "building_age", "area", "rooms", "lat", "lon",
                "floor", "floor_ratio", "is_last_floor", "district", "microdistrict_ppsm",
                "ceiling", "photos_count"]
    # rooms (-0.025) сильнее любой этажной части по отдельности, но слабее их суммы
    shap = np.array([-0.044, -0.039, 0.30, -0.025, 0.02, 0.015,
                     -0.01, -0.012, -0.008, 0.03, 0.01, 0.004, 0.001])
    base_log = 17.59
    fair = float(np.expm1(base_log + shap.sum()))

    factors, base, other = explain_price(shap, base_log, features, fair, n=5)

    keys = [f["feature"] for f in factors]
    assert len(keys) == len(set(keys)) == 5
    assert "year_built" not in keys and "lon" not in keys and "floor_ratio" not in keys
    assert "rooms" not in keys  # шестой после слияния — уходит в factors_other
    age = next(f for f in factors if f["feature"] == "building_age")
    assert age["impact"] == pytest.approx(-0.083)
    floor = next(f for f in factors if f["feature"] == "floor")
    assert floor["impact"] == pytest.approx(-0.03)
    assert next(f for f in factors if f["feature"] == "district")["impact"] == pytest.approx(0.04)
    assert next(f for f in factors if f["feature"] == "lat")["impact"] == pytest.approx(0.035)
    # база + показанные вклады + остальные == оценка, ровно (всё округлено как цена)
    assert base == round(float(np.expm1(base_log)), -4)
    assert base + sum(f["impact_tenge"] for f in factors) + other == round(fair, -4)


def test_explain_price_rent_rounds_to_thousands():
    from krisha.predict import explain_price

    shap = np.array([0.2, -0.05])
    base_log = float(np.log1p(250_000))
    fair = float(np.expm1(base_log + shap.sum()))

    factors, base, other = explain_price(shap, base_log, ["area", "rooms"], fair, deal="arenda")

    assert base == 250_000
    assert all(f["impact_tenge"] % 1_000 == 0 for f in factors)
    assert base + sum(f["impact_tenge"] for f in factors) + other == round(fair, -3)


# --- load_interval_models: миграционный фолбэк на legacy model_lo/model_hi
# (issue #132, доработка после ревью PR #138) -------------------------------

def _tiny_pool(n=40, seed=0):
    from catboost import Pool

    rng = np.random.default_rng(seed)
    x = rng.uniform(0, 1, size=(n, 3))
    y = x[:, 0] * 2 + x[:, 1] - x[:, 2] + rng.normal(0, 0.05, size=n)
    return Pool(x, y)


def _fit_tiny_quantile(alpha, seed=0):
    model = CatBoostRegressor(
        iterations=20, depth=2, loss_function=f"Quantile:alpha={alpha}",
        random_seed=seed, verbose=False,
    )
    model.fit(_tiny_pool(seed=seed))
    return model


def _fit_tiny_multiquantile(seed=0):
    model = CatBoostRegressor(
        iterations=20, depth=2, loss_function="MultiQuantile:alpha=0.1,0.9",
        random_seed=seed, verbose=False,
    )
    model.fit(_tiny_pool(seed=seed))
    return model


def test_load_interval_models_prefers_new_multiquantile_when_present(tmp_path, monkeypatch):
    import krisha.predict as predict_mod

    quantile_path = tmp_path / "model_quantile.cbm"
    _fit_tiny_multiquantile().save_model(str(quantile_path))
    lo_path, hi_path = tmp_path / "model_lo.cbm", tmp_path / "model_hi.cbm"
    _fit_tiny_quantile(0.1).save_model(str(lo_path))  # чтобы доказать: игнорируется
    _fit_tiny_quantile(0.9).save_model(str(hi_path))

    monkeypatch.setattr(predict_mod, "MODEL_QUANTILE_PATH", quantile_path)
    monkeypatch.setattr(predict_mod, "MODEL_LO_PATH", lo_path)
    monkeypatch.setattr(predict_mod, "MODEL_HI_PATH", hi_path)
    predict_mod.load_interval_models.cache_clear()

    model = predict_mod.load_interval_models()
    assert isinstance(model, CatBoostRegressor)
    pred = model.predict(_tiny_pool(n=5))
    assert pred.shape == (5, 2)
    predict_mod.load_interval_models.cache_clear()


def test_load_interval_models_falls_back_to_legacy_pair(tmp_path, monkeypatch):
    """model_quantile.cbm ещё не появился (до retrain) — используем старую
    пару model_lo/model_hi через обёртку с тем же (n, 2)-интерфейсом."""
    import krisha.predict as predict_mod

    lo_path, hi_path = tmp_path / "model_lo.cbm", tmp_path / "model_hi.cbm"
    _fit_tiny_quantile(0.1).save_model(str(lo_path))
    _fit_tiny_quantile(0.9).save_model(str(hi_path))

    monkeypatch.setattr(predict_mod, "MODEL_QUANTILE_PATH", tmp_path / "missing.cbm")
    monkeypatch.setattr(predict_mod, "MODEL_LO_PATH", lo_path)
    monkeypatch.setattr(predict_mod, "MODEL_HI_PATH", hi_path)
    predict_mod.load_interval_models.cache_clear()

    model = predict_mod.load_interval_models()
    assert isinstance(model, predict_mod._LegacyQuantilePair)
    pool = _tiny_pool(n=5)
    pred = model.predict(pool)
    assert pred.shape == (5, 2)
    # lo/hi должны совпадать с raw-предиктами исходных моделей построчно
    assert np.allclose(pred[:, 0], model._lo.predict(pool))
    assert np.allclose(pred[:, 1], model._hi.predict(pool))
    predict_mod.load_interval_models.cache_clear()


def test_load_interval_models_returns_none_when_nothing_available(tmp_path, monkeypatch):
    import krisha.predict as predict_mod

    monkeypatch.setattr(predict_mod, "MODEL_QUANTILE_PATH", tmp_path / "missing_q.cbm")
    monkeypatch.setattr(predict_mod, "MODEL_LO_PATH", tmp_path / "missing_lo.cbm")
    monkeypatch.setattr(predict_mod, "MODEL_HI_PATH", tmp_path / "missing_hi.cbm")
    predict_mod.load_interval_models.cache_clear()

    assert predict_mod.load_interval_models() is None
    predict_mod.load_interval_models.cache_clear()
