"""scripts/compare_models.py после refit на всех данных (аудит 24.10)."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import compare_models as cm  # noqa: E402


def test_eras_reach_current_prod():
    dates = [e["date"] for e in cm.ERAS]
    assert dates == sorted(dates)
    assert dates[-1] >= "2026-10-18"
    assert len({e["commit"] for e in cm.ERAS}) == len(cm.ERAS)


def test_era_cutoff_only_for_refit_metas():
    assert cm.era_data_cutoff({"metrics": {"model": {}}}) is None
    assert cm.era_data_cutoff(None) is None
    meta = {"metrics": {"final_fit": {"max_first_seen": "2026-10-18T00:00:00+00:00"}}}
    assert cm.era_data_cutoff(meta) == pd.Timestamp("2026-10-18T00:00:00+00:00")


def test_refit_era_that_saw_test_is_excluded(tmp_path):
    era = {"commit": "abc1234", "date": "2026-10-18", "label": "refit"}
    d = tmp_path / era["commit"]
    d.mkdir()
    (d / "model.cbm").write_bytes(b"not a model")  # до загрузки не дойдёт
    (d / "model_meta.json").write_text(json.dumps({
        "metrics": {"model": {"mape": 0.07},
                    "final_fit": {"max_first_seen": "2026-10-10T00:00:00+00:00"}},
    }), encoding="utf-8")
    first_seen = pd.to_datetime(pd.Series(["2026-10-05", "2026-10-12"]), utc=True)
    row = cm.evaluate_era(era, tmp_path, pd.DataFrame({"x": [1, 2]}), np.array([1.0, 2.0]), first_seen)
    assert row["mape"] is None
    assert "refit" in row["note"] and "1 из 2" in row["note"]
    assert row["own_reported_mape"] == 0.07
