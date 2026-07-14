import numpy as np
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
    factors = [{"feature": "area", "impact": 0.2}, {"feature": "floor", "impact": -0.1}]
    out = _with_money_impact(factors, fair)
    assert out[0]["impact_pct"] == round((np.expm1(0.2)) * 100, 1)  # +22.1%
    assert out[0]["impact_tenge"] == round(fair * (1 - np.exp(-0.2)), -4)
    assert out[1]["impact_pct"] < 0 and out[1]["impact_tenge"] < 0


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
