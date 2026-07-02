

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
