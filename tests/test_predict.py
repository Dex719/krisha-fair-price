

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
