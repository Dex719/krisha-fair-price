"""Прод-модель переобучается на всех данных; гейт сравнивает её преемника
только на строках, которых она не видела."""

import dataclasses
import json

import pandas as pd
import pytest
from catboost import CatBoostRegressor
from test_train_smoke import synthetic_df

from krisha.model_spec import SALE
from krisha.train import _unseen_by_old_model, train


def _tmp_spec(tmp_path):
    d = tmp_path / "models"
    return dataclasses.replace(
        SALE,
        models_dir=d,
        model_path=d / "model.cbm",
        quantile_path=d / "model_quantile.cbm",
        meta_path=d / "model_meta.json",
        spatial_ref_path=d / "spatial_ref.json",
        gate_samples_path=d / "model_gate_samples.json",
        metrics_history_path=d / "metrics_history.jsonl",
        shap_path=tmp_path / "reports" / "shap_summary.png",
    )


@pytest.fixture
def no_side_effects(monkeypatch):
    monkeypatch.setattr("krisha.zones.load_zone_index", lambda *a, **k: None)
    monkeypatch.setattr("krisha.stats.snapshot_stats", lambda *a, **k: None)


def _old_meta_with_cutoff(tmp_path, spec, cutoff):
    meta = json.loads(spec.meta_path.read_text(encoding="utf-8"))
    meta["metrics"]["final_fit"]["max_first_seen"] = cutoff
    path = tmp_path / "old_meta.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    return path


def test_saved_model_is_refit_on_all_rows_and_gate_compares_only_unseen(
    tmp_path, no_side_effects, monkeypatch,
):
    monkeypatch.setattr("krisha.train.GATE_MIN_UNSEEN_ROWS", 5)  # синтетика маленькая
    df = synthetic_df(n=500)
    spec = _tmp_spec(tmp_path)

    metrics = train(df=df, iterations=80, save=True, spec=spec)

    final = metrics["final_fit"]
    # метрики — модели без test-окна, в прод — модель на train + test
    assert final["n_rows"] >= metrics["n_train"] + metrics["n_test"]
    saved = CatBoostRegressor()
    saved.load_model(str(spec.model_path))
    assert saved.tree_count_ == final["best_iterations"]
    assert pd.Timestamp(final["max_first_seen"]) == pd.to_datetime(df["first_seen"], utc=True).max()
    meta = json.loads(spec.meta_path.read_text(encoding="utf-8"))
    assert meta["metrics"]["final_fit"] == final

    # Следующий ретрейн: прошлая модель видела данные до середины test-окна —
    # сравнение только на более свежих строках, и в гейт-сэмплах их столько же
    test_from = pd.Timestamp(metrics["test_window"]["from"], tz="UTC")
    test_to = pd.Timestamp(metrics["test_window"]["to"], tz="UTC")
    cutoff = test_from + (test_to - test_from) / 2
    old_meta = _old_meta_with_cutoff(tmp_path, spec, cutoff.isoformat())
    old_model = tmp_path / "old_model.cbm"
    old_model.write_bytes(spec.model_path.read_bytes())

    again = train(
        df=df, iterations=80, save=True, spec=spec,
        old_model_path=old_model, old_meta_path=old_meta,
    )

    assert "old_model_error" not in again, again.get("old_model_error")
    seen_ts = pd.to_datetime(df["first_seen"], utc=True)
    newer = int(((seen_ts > cutoff) & (seen_ts >= test_from)).sum())
    assert 0 < again["old_model_rows"] < again["n_test"]
    assert again["old_model_rows"] <= newer
    assert set(again["model_vs_old"]) == set(again["model"])
    samples = json.loads(spec.gate_samples_path.read_text(encoding="utf-8"))
    assert len(samples["ape_new"]) == len(samples["ape_old"]) == again["old_model_rows"]


def test_gate_fails_closed_when_old_model_saw_the_whole_test(tmp_path, no_side_effects):
    df = synthetic_df(n=400)
    spec = _tmp_spec(tmp_path)
    train(df=df, iterations=60, save=True, spec=spec)
    old_meta = _old_meta_with_cutoff(tmp_path, spec, "2099-01-01T00:00:00+00:00")

    metrics = train(
        df=df, iterations=60, save=False, spec=spec,
        old_model_path=spec.model_path, old_meta_path=old_meta,
    )

    assert "сравнивать не на чем" in metrics["old_model_error"]


def test_unseen_mask_absent_for_models_trained_without_test_window(tmp_path):
    raw_test = pd.DataFrame({"first_seen": ["2026-03-01", "2026-03-05"]})
    old_meta = tmp_path / "meta.json"
    old_meta.write_text(json.dumps({"metrics": {"model": {}}}), encoding="utf-8")
    assert _unseen_by_old_model(raw_test, old_meta) is None
    assert _unseen_by_old_model(raw_test, None) is None

    old_meta.write_text(json.dumps(
        {"metrics": {"final_fit": {"max_first_seen": "2026-03-02T00:00:00+00:00"}}}
    ), encoding="utf-8")
    assert _unseen_by_old_model(raw_test, old_meta).tolist() == [False, True]
