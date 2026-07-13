"""Тесты scripts/model_gate.py (issue #106): coverage-гейт, fail-closed, бутстреп."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import model_gate as gate  # noqa: E402


def _meta(model, interval=None, old_model=None, old_model_error=None):
    metrics = {"model": model}
    if interval is not None:
        metrics["interval"] = interval
    if old_model is not None:
        metrics["old_model"] = old_model
    if old_model_error is not None:
        metrics["old_model_error"] = old_model_error
    return {"metrics": metrics}


OLD_MODEL = {"mae": 5_000_000, "mape": 0.10, "r2": 0.85}
OLD_INTERVAL = {"target_coverage": 0.80, "coverage_test": 0.80, "median_width_pct": 0.20}


def _run(tmp_path, old_meta, new_meta, samples=None):
    old_path = tmp_path / "old_meta.json"
    new_path = tmp_path / "new_meta.json"
    old_path.write_text(json.dumps(old_meta))
    new_path.write_text(json.dumps(new_meta))
    argv = ["model_gate.py", str(old_path), str(new_path)]
    samples_path = tmp_path / "model_gate_samples.json"
    if samples is not None:
        samples_path.write_text(json.dumps(samples))
    argv += ["--samples", str(samples_path)]
    old_argv = sys.argv
    sys.argv = argv
    try:
        with pytest.raises(SystemExit) as exc:
            gate.main()
        return exc.value.code
    finally:
        sys.argv = old_argv


def test_passes_when_new_model_is_better(tmp_path):
    old = _meta(OLD_MODEL, interval=OLD_INTERVAL)
    new = _meta(
        {"mae": 4_800_000, "mape": 0.09, "r2": 0.87},
        interval={"target_coverage": 0.80, "coverage_test": 0.82, "median_width_pct": 0.19},
        old_model=OLD_MODEL,
    )
    assert _run(tmp_path, old, new) == 0


def test_fails_closed_on_old_model_error(tmp_path):
    """issue #106: сравнение со старой моделью не удалось — блокируем, не fallback."""
    old = _meta(OLD_MODEL, interval=OLD_INTERVAL)
    new = _meta(
        {"mae": 4_800_000, "mape": 0.09, "r2": 0.87},
        interval={"target_coverage": 0.80, "coverage_test": 0.82, "median_width_pct": 0.19},
        old_model_error="feature mismatch: missing column 'foo'",
    )
    assert _run(tmp_path, old, new) == 1


def test_fails_when_coverage_drops_below_tolerance(tmp_path):
    """issue #106: гейт должен ловить провалившееся покрытие интервала, даже
    если точечные MAE/MAPE не деградировали."""
    old = _meta(OLD_MODEL, interval=OLD_INTERVAL)
    new = _meta(
        {"mae": 4_800_000, "mape": 0.09, "r2": 0.87},  # точечная модель лучше
        interval={"target_coverage": 0.80, "coverage_test": 0.60, "median_width_pct": 0.19},
        old_model=OLD_MODEL,
    )
    assert _run(tmp_path, old, new) == 1


def test_fails_when_interval_width_balloons(tmp_path):
    old = _meta(OLD_MODEL, interval=OLD_INTERVAL)
    new = _meta(
        {"mae": 4_800_000, "mape": 0.09, "r2": 0.87},
        interval={"target_coverage": 0.80, "coverage_test": 0.81, "median_width_pct": 0.50},
        old_model=OLD_MODEL,
    )
    assert _run(tmp_path, old, new) == 1


def test_bootstrap_catches_significant_ape_regression(tmp_path):
    """Плоский допуск (±0.5пп MAPE) пропустил бы это как шум — бутстреп на
    парных APE видит устойчивую деградацию по всем строкам."""
    old = _meta(OLD_MODEL, interval=OLD_INTERVAL)
    new = _meta(
        {"mae": 5_050_000, "mape": 0.104, "r2": 0.84},  # +0.4пп — в пределах плоского допуска
        interval=OLD_INTERVAL,
        old_model=OLD_MODEL,
    )
    # Каждая test-строка стабильно на 2 п.п. хуже у новой модели — не шум
    ape_old = [0.10] * 500
    ape_new = [0.12] * 500
    assert _run(tmp_path, old, new, samples={"ape_new": ape_new, "ape_old": ape_old}) == 1


def test_bootstrap_allows_noisy_improvement(tmp_path):
    old = _meta(OLD_MODEL, interval=OLD_INTERVAL)
    new = _meta(
        {"mae": 4_900_000, "mape": 0.099, "r2": 0.86},
        interval=OLD_INTERVAL,
        old_model=OLD_MODEL,
    )
    import numpy as np

    rng = np.random.default_rng(0)
    ape_old = list(rng.normal(0.10, 0.05, 500).clip(min=0.01))
    ape_new = list((np.array(ape_old) - 0.002))  # немного лучше, в пределах шума
    assert _run(tmp_path, old, new, samples={"ape_new": ape_new, "ape_old": ape_old}) == 0


def test_fallback_without_compare_old_uses_old_meta(tmp_path):
    """Нет --compare-old (нет old_model в new_meta) — сравнение с прошлым meta."""
    old = _meta(OLD_MODEL, interval=OLD_INTERVAL)
    new = _meta(
        {"mae": 4_800_000, "mape": 0.09, "r2": 0.87},
        interval={"target_coverage": 0.80, "coverage_test": 0.81, "median_width_pct": 0.19},
    )
    assert _run(tmp_path, old, new) == 0
