"""train.py после аудита 24.10: старая модель без колонки, перевыставления в
сравнении со старой, CQR с запасом, временной early stopping, квантильная
модель на всех данных, ширина старого интервала на том же тесте."""

import json

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from test_train_refit import _old_meta_with_cutoff, _tmp_spec, no_side_effects  # noqa: F401
from test_train_smoke import synthetic_df

import krisha.train as tr
from krisha.features import MISSING_CAT


def test_old_model_pool_fills_columns_removed_from_features(caplog):
    # старая модель знает признаки, которые новый build_features уже не
    # считает — раньше KeyError → old_model_error → вечный блок гейта
    rng = np.random.default_rng(0)
    train = pd.DataFrame({
        "a": rng.normal(size=200), "gone_num": rng.normal(size=200),
        "c": rng.choice(["x", "y"], 200), "gone_cat": rng.choice(["p", "q"], 200),
    })
    model = CatBoostRegressor(iterations=5, verbose=False)
    model.fit(Pool(train, rng.normal(size=200), cat_features=["c", "gone_cat"]))
    test_df = train[["a", "c"]].head(10)

    pool = tr._old_model_pool(model, test_df, None)

    assert len(model.predict(pool)) == 10
    assert "gone_num" in caplog.text and "gone_cat" in caplog.text
    assert MISSING_CAT == "unknown"


def _flat(fs, lat, area=50.0):
    return {"district": "Bostandykskiy_r-n", "rooms": 2, "area": area, "floor": 3,
            "total_floors": 9, "lat": lat, "lon": 76.9, "first_seen": fs}


def test_unseen_excludes_relistings_old_model_saw(tmp_path):
    meta = tmp_path / "old_meta.json"
    meta.write_text(json.dumps(
        {"metrics": {"final_fit": {"max_first_seen": "2026-03-10T00:00:00+00:00"}}}
    ), encoding="utf-8")
    raw_test = pd.DataFrame([
        _flat("2026-03-05", 43.2000),  # старее отсечки — старая видела
        _flat("2026-03-12", 43.2100),  # перевыставление квартиры, виденной под другим id
        _flat("2026-03-12", 43.2200),  # действительно новая
    ])
    # история ДО дедупа: старое появление квартиры 43.21 — до отсечки
    history = pd.concat([raw_test, pd.DataFrame([_flat("2026-02-20", 43.2100)])], ignore_index=True)

    assert tr._unseen_by_old_model(raw_test, meta).tolist() == [False, True, True]
    assert tr._unseen_by_old_model(raw_test, meta, history).tolist() == [False, False, True]


def test_cqr_scale_calibrates_above_target_with_cluster_count():
    scores = np.linspace(0, 1, 1001)
    plain = tr._cqr_scale(scores)
    # цель 0.80 + запас CQR_COVERAGE_MARGIN, а не ровно 0.80
    assert plain >= np.quantile(scores, tr.INTERVAL_TARGET_COVERAGE + tr.CQR_COVERAGE_MARGIN)
    assert tr.CQR_COVERAGE_MARGIN > 0
    # 1001 строка, но 50 зданий: поправка на конечную выборку по зданиям шире
    clusters = np.repeat(np.arange(50), 21)[:1001]
    assert tr._cqr_scale(scores, clusters) > plain
    assert tr._cqr_scale(np.array([])) == 0.0


def test_point_early_stopping_validates_on_latest_rows(monkeypatch):
    monkeypatch.setattr("krisha.zones.load_zone_index", lambda *a, **k: None)
    calls = []
    real = tr.build_features

    def spy(frame, **kw):
        fs = pd.to_datetime(frame["first_seen"], utc=True)
        calls.append((fs.min(), fs.max(), kw.get("knn_self_indices") is not None))
        return real(frame, **kw)

    monkeypatch.setattr(tr, "build_features", spy)
    df = tr.prepare_frame(synthetic_df(n=400))
    tr.fit_point_model(df, iterations=30, verbose=False)

    # вызовы: весь train, fit-часть ES (с self-индексами), val ES (без)
    _, fit_max, _ = calls[1]
    val_min, _, val_self = calls[2]
    assert not val_self
    assert val_min > fit_max  # val — строго свежее fit: срез по времени, а не по зданиям


def test_refit_quantile_on_all_data_and_old_interval_on_same_test(
    tmp_path, no_side_effects, monkeypatch,  # noqa: F811
):
    monkeypatch.setattr("krisha.train.GATE_MIN_UNSEEN_ROWS", 5)
    monkeypatch.setattr("krisha.train.QUANTILE_ITERATIONS", 60)
    df = synthetic_df(n=500)
    spec = _tmp_spec(tmp_path)

    first = tr.train(df=df, iterations=60, save=True, spec=spec)

    interval = first["interval"]
    q = CatBoostRegressor()
    q.load_model(str(spec.quantile_path))
    # в прод — квантильная модель на всех данных с найденным числом деревьев
    assert interval["final_fit"] == {"data": "train+test", "iterations": interval["quantile_best_iterations"]}
    assert q.tree_count_ == interval["quantile_best_iterations"]
    assert interval["calibration_coverage"] > interval["target_coverage"]

    # следующий ретрейн: рядом со старой точечной лежит старая квантильная
    test_from = pd.Timestamp(first["test_window"]["from"], tz="UTC")
    test_to = pd.Timestamp(first["test_window"]["to"], tz="UTC")
    old_meta = _old_meta_with_cutoff(tmp_path, spec, (test_from + (test_to - test_from) / 2).isoformat())
    old_model = tmp_path / "old_model.cbm"
    old_model.write_bytes(spec.model_path.read_bytes())
    (tmp_path / "old_model_quantile.cbm").write_bytes(spec.quantile_path.read_bytes())

    again = tr.train(df=df, iterations=60, save=True, spec=spec,
                     old_model_path=old_model, old_meta_path=old_meta)

    assert "old_model_error" not in again
    old_iv = again["old_model_interval"]
    assert 0 < old_iv["median_width_pct"] < 5 and 0 <= old_iv["coverage_test"] <= 1
    samples = json.loads(spec.gate_samples_path.read_text(encoding="utf-8"))
    assert len(samples["clusters"]) == len(samples["ape_new"]) == again["old_model_rows"]
