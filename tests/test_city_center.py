"""Точка «центра города»: исправлена, но старые модели видят ту, на которой учились.

До 10.2026 ALMATY_CENTER стоял ~5 км западнее пересечения Абая/Достык.
Модель выучила расстояние до той точки, поэтому сменить константу «просто
так» нельзя: до ближайшего ретрейна прод-модель получала бы чужие расстояния.
"""

import json

import pandas as pd
import pytest
from catboost import CatBoostRegressor

from krisha import train as train_mod
from krisha.config import ALMATY_CENTER, LEGACY_ALMATY_CENTER
from krisha.features import build_features, haversine_km, model_center

ABAY_DOSTYK = (43.2399, 76.9570)
REPUBLIC_SQUARE = (43.2383, 76.9453)


def test_center_is_abay_dostyk():
    assert ALMATY_CENTER == ABAY_DOSTYK
    # площадь Республики — центр, а не «в 4.5 км от центра», как было
    assert haversine_km(*REPUBLIC_SQUARE, *ALMATY_CENTER) < 1.5
    assert haversine_km(*REPUBLIC_SQUARE, *LEGACY_ALMATY_CENTER) > 4


def test_model_center_falls_back_to_legacy_for_old_metas():
    assert model_center({}) == LEGACY_ALMATY_CENTER
    assert model_center(None) == LEGACY_ALMATY_CENTER
    assert model_center({"city_center": list(ALMATY_CENTER)}) == ALMATY_CENTER


def test_build_features_uses_given_center():
    df = pd.DataFrame([{"lat": ABAY_DOSTYK[0], "lon": ABAY_DOSTYK[1], "price": 1, "area": 50}])
    assert build_features(df)["dist_center_km"].iloc[0] == pytest.approx(0, abs=0.01)
    legacy = build_features(df, center=LEGACY_ALMATY_CENTER)["dist_center_km"].iloc[0]
    assert legacy == pytest.approx(haversine_km(*ABAY_DOSTYK, *LEGACY_ALMATY_CENTER), abs=0.01)


def test_gate_scores_old_model_on_its_own_features_and_center(tmp_path):
    """Гейт оценивает прошлую модель на новом test. Её признаки — по её
    feature_names_ (лишние и новые колонки не мешают), dist_center_km — от
    её точки центра: у меты без city_center это LEGACY_ALMATY_CENTER."""
    x = pd.DataFrame({"dist_center_km": [0.5, 2.0, 5.0, 9.0] * 10, "district": ["a", "b"] * 20})
    old = CatBoostRegressor(iterations=30, depth=2, verbose=0, random_seed=0)
    old.fit(x, x["dist_center_km"] * 10, cat_features=["district"])
    test_df = pd.DataFrame({
        "lat": [ABAY_DOSTYK[0]], "lon": [ABAY_DOSTYK[1]], "district": ["a"],
        "dist_center_km": [0.0],          # посчитано от нового центра
        "brand_new_feature": [1.0],       # у старой модели такого нет
    })
    old_meta = tmp_path / "old_meta.json"
    old_meta.write_text(json.dumps({"features": ["dist_center_km", "district"]}), encoding="utf-8")

    pool = train_mod._old_model_pool(old, test_df, old_meta)

    legacy_dist = haversine_km(*ABAY_DOSTYK, *LEGACY_ALMATY_CENTER)
    expected = old.predict(pd.DataFrame({"dist_center_km": [legacy_dist], "district": ["a"]}))
    assert old.predict(pool) == pytest.approx(expected)
    # мета уже с новым центром — колонку не пересчитываем
    old_meta.write_text(json.dumps({"city_center": list(ALMATY_CENTER)}), encoding="utf-8")
    same = old.predict(train_mod._old_model_pool(old, test_df, old_meta))
    assert same == pytest.approx(old.predict(pd.DataFrame({"dist_center_km": [0.0], "district": ["a"]})))
