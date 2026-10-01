"""Настоящие артефакты модели аренды в models/rent/ — согласованность meta ↔ модель.

Весов нет в git: retrain.yml гоняет этот файл на свежеобученной модели до
публикации, а в CI и в свежем клоне он пропускается.
"""

import json

import pytest
from catboost import CatBoostRegressor

from krisha.model_spec import RENT

pytestmark = pytest.mark.skipif(
    not (RENT.model_path.exists() and RENT.meta_path.exists()),
    reason="артефактов модели аренды нет (свежий клон / CI без релиза)",
)


@pytest.fixture(scope="module")
def meta():
    return json.loads(RENT.meta_path.read_text(encoding="utf-8"))


def test_meta_describes_rent_model(meta):
    assert meta["deal"] == "arenda"
    assert meta["features"] == RENT.all_features
    assert meta["cat_features"] == list(RENT.cat_features)


def test_model_trained_on_meta_features(meta):
    model = CatBoostRegressor()
    model.load_model(str(RENT.model_path))
    assert list(model.feature_names_) == meta["features"]


def test_quantile_model_matches_features(meta):
    if not RENT.quantile_path.exists():
        pytest.skip("квантильной модели аренды нет")
    model = CatBoostRegressor()
    model.load_model(str(RENT.quantile_path))
    assert list(model.feature_names_) == meta["features"]


def test_metrics_are_plausible_for_rent(meta):
    """Порядок величин: MAE аренды — десятки тысяч ₸/мес, не миллионы ₸.
    Перепутанная база (продажа под видом аренды) вылезла бы здесь."""
    m = meta["metrics"]
    assert 5_000 < m["model"]["mae"] < 300_000
    assert m["model"]["mape"] < m["baseline"]["mape"]
    assert m["model"]["mape"] < 0.20
    assert RENT.spatial_ref_path.exists()
