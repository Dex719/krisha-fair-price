"""Гейт после аудита 24.10: кластерный бутстреп, узкий допуск, защита от
накопления по истории, ширина старого интервала на том же тесте."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import model_gate as gate  # noqa: E402

OLD = {"mae": 5_000_000, "mape": 0.0700, "r2": 0.9}
INTERVAL = {"target_coverage": 0.80, "coverage_test": 0.80, "median_width_pct": 0.24}
TRAINED_AT = "2026-10-25T10:00:00+00:00"


def _run(tmp_path, new_metrics, *, samples=None, history=None, old_interval=INTERVAL, capsys=None):
    old_meta = {"metrics": {"model": OLD, "interval": old_interval}}
    new_meta = {"metrics": {"trained_at": TRAINED_AT, "interval": INTERVAL, "old_model": OLD, **new_metrics}}
    (tmp_path / "old_meta.json").write_text(json.dumps(old_meta))
    (tmp_path / "new_meta.json").write_text(json.dumps(new_meta))
    samples_path = tmp_path / "model_gate_samples.json"
    if samples is not None:
        samples_path.write_text(json.dumps(samples))
    if history is not None:
        (tmp_path / "metrics_history.jsonl").write_text(
            "\n".join(json.dumps(r) for r in history) + "\n"
        )
    argv = ["model_gate.py", str(tmp_path / "old_meta.json"), str(tmp_path / "new_meta.json"),
            "--samples", str(samples_path)]
    old_argv, sys.argv = sys.argv, argv
    try:
        with pytest.raises(SystemExit) as exc:
            gate.main()
        return exc.value.code
    finally:
        sys.argv = old_argv


def _clustered_samples(shift: float, n_clusters=1500, per=3, seed=0, with_clusters=True):
    """Пары APE, где строки одного здания коррелируют (близнецы)."""
    rng = np.random.default_rng(seed)
    base = rng.gamma(2.0, 0.035, n_clusters).repeat(per)
    noise_cluster = rng.normal(0, 0.02, n_clusters).repeat(per)
    ape_old = np.abs(base + rng.normal(0, 0.005, base.size))
    ape_new = ape_old + noise_cluster + shift  # без abs: точка разницы ≈ shift
    out = {"ape_new": ape_new.tolist(), "ape_old": ape_old.tolist()}
    if with_clusters:
        out["clusters"] = [f"b{i}" for i in range(n_clusters) for _ in range(per)]
    return out


def test_cluster_bootstrap_is_wider_than_rowwise_on_twins():
    s = _clustered_samples(0.0)
    lo_r, hi_r, _ = gate.bootstrap_ape_diff(s["ape_new"], s["ape_old"])
    lo_c, hi_c, _ = gate.bootstrap_ape_diff(s["ape_new"], s["ape_old"], clusters=s["clusters"])
    assert (hi_c - lo_c) > 1.4 * (hi_r - lo_r)


def test_cluster_bootstrap_rejects_mismatched_labels():
    with pytest.raises(ValueError):
        gate.bootstrap_ape_diff([0.1, 0.2], [0.1, 0.2], clusters=["a"])


def test_degradation_of_03pp_is_blocked_now(tmp_path):
    # при старом допуске 0.5 п.п. это проходило — 4 таких ретрейна = +1.2 п.п.
    s = _clustered_samples(0.003)
    new = {"mae": OLD["mae"], "mape": 0.073, "r2": 0.9}
    assert _run(tmp_path, {"model": new}, samples=s) == 1


def test_noise_level_difference_passes(tmp_path):
    s = _clustered_samples(0.0002)
    new = {"mae": OLD["mae"], "mape": 0.0702, "r2": 0.9}
    assert _run(tmp_path, {"model": new}, samples=s) == 0


def test_samples_without_clusters_use_wider_rowwise_tolerance(tmp_path, capsys):
    # 0.25 п.п.: по кластерам — блок, без кластеров (старый train.py) — мягкий режим
    new = {"mae": OLD["mae"], "mape": 0.0725, "r2": 0.9}
    with_cl = tmp_path / "with"
    with_cl.mkdir()
    assert _run(with_cl, {"model": new}, samples=_clustered_samples(0.0025)) == 1
    s = _clustered_samples(0.0025, with_clusters=False)
    capsys.readouterr()
    assert _run(tmp_path, {"model": new}, samples=s) == 0
    assert "нет кластеров" in capsys.readouterr().out


def _history(*mapes):
    return [
        {"trained_at": f"2026-10-0{i + 1}T10:00:00+00:00", "mape": m, "mae": 1, "r2": 0.9}
        for i, m in enumerate(mapes)
    ]


def test_history_blocks_slow_accumulation_when_also_worse_than_old(tmp_path):
    # каждую неделю «чуть хуже в допуске» → против лучшей из 4 уже +0.8 п.п.
    new = {"mae": OLD["mae"], "mape": 0.078, "r2": 0.9}
    old = {**OLD, "mape": 0.0775}
    code = _run(tmp_path, {"model": new, "old_model": old},
                history=_history(0.070, 0.0725, 0.075, 0.0775))
    assert code == 1


def test_history_does_not_block_when_new_beats_old(tmp_path):
    # лёгкий тест прошлой недели не должен навсегда закрыть дорогу: новая
    # лучше прод-модели на общем тесте → пропускаем, даже если выше истории
    new = {"mae": OLD["mae"], "mape": 0.078, "r2": 0.9}
    old = {**OLD, "mape": 0.079}
    assert _run(tmp_path, {"model": new, "old_model": old}, history=_history(0.070, 0.071)) == 0


def test_history_ignores_own_record_and_missing_file(tmp_path):
    new = {"mae": OLD["mae"], "mape": 0.0705, "r2": 0.9}
    # своя запись (trained_at новой) уже в истории — её не считаем
    own = {"trained_at": TRAINED_AT, "mape": 0.060, "mae": 1, "r2": 0.9}
    assert _run(tmp_path, {"model": new}, history=_history(0.070) + [own]) == 0
    other = tmp_path / "fresh"
    other.mkdir()
    assert _run(other, {"model": new}) == 0  # нет истории — проверка пропущена


def test_history_tolerance_matches_real_weeks():
    # реальная история продажи 02.08–18.10: ни один ретрейн не задел бы порог
    sale = [.0849, .0766, .0758, .0729, .0749, .0733, .0769, .0775, .0756, .0702, .0722]
    for i in range(1, len(sale)):
        hist = [{"mape": m} for m in sale[max(0, i - gate.HISTORY_WINDOW):i]]
        ok, _ = gate.history_check(sale[i], hist, worse_than_old=True)
        assert ok, (i, sale[i])


def test_width_compared_on_same_test_when_available(tmp_path, capsys):
    # в старой мете ширина 0.20 (другой тест) — по ней новая 0.24 «раздулась»;
    # на том же тесте старая 0.235 — новая в допуске
    new = {"mae": OLD["mae"], "mape": 0.070, "r2": 0.9}
    narrow_old = {**INTERVAL, "median_width_pct": 0.20}
    code = _run(tmp_path, {"model": new, "old_model_interval": {"median_width_pct": 0.235}},
                old_interval=narrow_old)
    assert code == 0
    assert "на том же test" in capsys.readouterr().out


def test_width_falls_back_to_old_meta_with_warning(tmp_path, capsys):
    new = {"mae": OLD["mae"], "mape": 0.070, "r2": 0.9}
    code = _run(tmp_path, {"model": new}, old_interval={**INTERVAL, "median_width_pct": 0.20})
    out = capsys.readouterr().out
    assert code == 1
    assert "из старой меты" in out
