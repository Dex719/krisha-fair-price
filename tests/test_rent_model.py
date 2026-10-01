"""Модель аренды: спецификация, арендные признаки, фильтр подселений, обучение."""

import dataclasses
import json

import numpy as np
import pandas as pd
import pytest

from krisha import db_release, monitoring
from krisha.features import (
    ALL_FEATURES,
    CAT_FEATURES,
    MISSING_CAT,
    RENT_CAT_EXTRA,
    RENT_NUM_EXTRA,
    add_raw_param_features,
    clean,
)
from krisha.model_spec import RENT, SALE, spec_for
from krisha.rent import drop_room_shares, is_room_share
from krisha.train import train

RENT_PARAMS = {
    "flat.rent_renovation": "свежий ремонт, новая мебель",
    "separated_toilet": "совмещен",
    "bathroom": "душевая кабина, ванна",
    "live.furniture": "полностью",
    "flat.facilities": "холодильник, стиральная машина, кондиционер, лифт",
    "flat.furniture": "кровать, диван, шкаф для одежды",
    "who_match": "семейной паре, можно с детьми",
    "window_side": "во двор",
    "balcony_count": "1",
    "loggia_count": "нет",
    "toilet_count": "2",
    "kitchen_studio": "да",
}
SALE_PARAMS = {
    "flat.renovation": "свежий ремонт",
    "flat.toilet": "раздельный",
    "flat.balcony": "балкон",
    "live.furniture": "частично",
    "has_change": "Нет",
}


def _frame(params: dict) -> pd.DataFrame:
    return pd.DataFrame({"raw_params": [json.dumps(params, ensure_ascii=False)]})


# --- спецификация --------------------------------------------------------------


def test_sale_feature_set_is_unchanged():
    """Продажная модель обязана учиться ровно на прежних фичах: гейт сравнивает
    новую модель со старой на одном тесте, и чужой признак его бы уронил."""
    assert SALE.all_features == ALL_FEATURES
    assert list(SALE.cat_features) == CAT_FEATURES
    assert not set(RENT_NUM_EXTRA + RENT_CAT_EXTRA) & set(SALE.all_features)


def test_rent_spec_has_its_own_artifacts_and_bounds():
    assert set(RENT_NUM_EXTRA + RENT_CAT_EXTRA) <= set(RENT.all_features)
    assert set(RENT_CAT_EXTRA) <= set(RENT.cat_features)
    sale_paths = {SALE.model_path, SALE.quantile_path, SALE.meta_path, SALE.spatial_ref_path,
                  SALE.gate_samples_path, SALE.metrics_history_path, SALE.shap_path}
    rent_paths = {RENT.model_path, RENT.quantile_path, RENT.meta_path, RENT.spatial_ref_path,
                  RENT.gate_samples_path, RENT.metrics_history_path, RENT.shap_path}
    assert not sale_paths & rent_paths, "ретрейн аренды не должен трогать файлы продажи"
    assert RENT.model_path.parent.name == "rent"
    assert RENT.ppsm_bounds[1] < SALE.ppsm_bounds[0]  # ₸/м² в месяц против ₸/м²
    assert spec_for("arenda") is RENT and spec_for("prodazha") is SALE
    with pytest.raises(ValueError):
        spec_for("posutochno")


# --- признаки ------------------------------------------------------------------


def test_rent_params_fill_renovation_toilet_balcony_and_extras():
    row = add_raw_param_features(_frame(RENT_PARAMS)).iloc[0]
    assert row["renovation"] == "свежий ремонт, новая мебель"
    assert row["toilet"] == "совмещен"
    assert row["balcony"] == "b1_l0"
    assert row["bathroom"] == "душевая кабина, ванна"
    assert row["window_side"] == "во двор"
    assert row["n_facilities"] == 4 and row["n_furniture_items"] == 3
    assert row["fac_aircon"] == 1 and row["fac_elevator"] == 1 and row["fac_dishwasher"] == 0
    assert row["who_kids"] == 1 and row["who_family"] == 1 and row["who_pets"] == 0
    assert row["balcony_n"] == 1 and row["loggia_n"] == 0 and row["toilet_count"] == 2
    assert row["kitchen_studio"] == 1


def test_sale_listing_features_are_untouched_by_rent_fallbacks():
    """У продажного лота арендных ключей нет: ремонт/санузел/балкон берутся
    из продажных ключей, а арендные флаги — «неизвестно», не «нет»."""
    row = add_raw_param_features(_frame(SALE_PARAMS)).iloc[0]
    assert row["renovation"] == "свежий ремонт"
    assert row["toilet"] == "раздельный"
    assert row["balcony"] == "балкон"
    assert np.isnan(row["fac_aircon"]) and np.isnan(row["who_kids"]) and np.isnan(row["n_facilities"])
    assert row["bathroom"] == MISSING_CAT


def test_clean_uses_rent_bounds():
    df = pd.DataFrame({"price": [300_000, 50_000_000, 5_000], "area": [50.0, 50.0, 50.0]})
    assert clean(df, RENT.price_bounds, RENT.ppsm_bounds)["price"].tolist() == [300_000]
    assert clean(df)["price"].tolist() == [50_000_000]  # по умолчанию — продажа


# --- подселение --------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Ищем третью девушку на подселение в 2-комнатную квартиру",
    "Сдаётся комната в тихом районе",
    "Сдам комнату девушке, 100 000 тг",
    "Бірге тұратын бір қыз керек",
    "Койко-место для студентов",
])
def test_room_share_detected(text):
    assert is_room_share(text)


@pytest.mark.parametrize("text", [
    "2 бөлмелі пәтер жалға беріледі, отбасына",
    "Сдается уютная 2-комнатная квартира, есть вся мебель",
    None,
    "",
])
def test_whole_flat_is_not_room_share(text):
    assert not is_room_share(text)


def test_drop_room_shares():
    df = pd.DataFrame({"description": ["Ищу соседку на подселение", "Сдам квартиру на долгий срок", None]})
    assert drop_room_shares(df)["description"].tolist()[0] == "Сдам квартиру на долгий срок"
    assert len(drop_room_shares(df)) == 2


# --- обучение ------------------------------------------------------------------

rng = np.random.default_rng(7)


def synthetic_rent_df(n=400, days_span=90):
    area = rng.uniform(25, 110, n)
    rooms = np.clip((area / 28).astype(int), 1, 4)
    district = rng.choice(["Bostandykskiy_r-n", "Alatauskiy_r-n", "Medeuskiy_r-n"], n)
    fresh = rng.random(n) < 0.5
    ppsm = np.where(district == "Medeuskiy_r-n", 7_500, 5_000) * np.where(fresh, 1.15, 1.0)
    ppsm = ppsm + rng.normal(0, 250, n)
    first_seen = pd.Timestamp("2026-07-01") + pd.to_timedelta(rng.integers(0, days_span, n), unit="D")
    raw = [
        json.dumps({**RENT_PARAMS, "flat.rent_renovation": "свежий ремонт, новая мебель" if f
                    else "не новый, но аккуратный и чистый ремонт"}, ensure_ascii=False)
        for f in fresh
    ]
    description = ["Сдаётся квартира на длительный срок"] * n
    description[0] = "Ищу девушку на подселение"  # подселение — в обучение не попадёт
    return pd.DataFrame({
        "id": np.arange(1, n + 1),
        "price": (np.round(area * ppsm / 10_000) * 10_000).astype(int),
        "area": area,
        "rooms": rooms,
        "district": district,
        "floor": rng.integers(1, 10, n),
        "total_floors": 10,
        "lat": 43.24 + rng.normal(0, 0.03, n),
        "lon": 76.89 + rng.normal(0, 0.03, n),
        "photos_count": rng.integers(1, 15, n),
        "first_seen": first_seen.astype(str),
        "raw_params": raw,
        "description": description,
    })


def _tmp_spec(tmp_path):
    d = tmp_path / "models" / "rent"
    return dataclasses.replace(
        RENT,
        models_dir=d,
        model_path=d / "model.cbm",
        quantile_path=d / "model_quantile.cbm",
        meta_path=d / "model_meta.json",
        spatial_ref_path=d / "spatial_ref.json",
        gate_samples_path=d / "model_gate_samples.json",
        metrics_history_path=d / "metrics_history.jsonl",
        shap_path=tmp_path / "reports" / "shap_summary_rent.png",
    )


def test_rent_training_writes_only_rent_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr("krisha.zones.load_zone_index", lambda *a, **k: None)
    snapshot = []
    monkeypatch.setattr("krisha.stats.snapshot_stats", lambda *a, **k: snapshot.append(1))
    spec = _tmp_spec(tmp_path)

    metrics = train(df=synthetic_rent_df(), iterations=100, save=True, spec=spec)

    assert metrics["model"]["r2"] > 0.5
    assert metrics["model"]["mape"] < 0.2
    # подселение отфильтровано до сплита
    assert metrics["n_train"] + metrics["n_test"] + metrics["n_purged"] == 399
    meta = json.loads(spec.meta_path.read_text(encoding="utf-8"))
    assert meta["deal"] == "arenda"
    assert meta["features"] == RENT.all_features
    for path in (spec.model_path, spec.quantile_path, spec.spatial_ref_path, spec.metrics_history_path):
        assert path.exists(), path
    assert snapshot == [], "снапшот /api/stats — продажный, аренда его не пишет"


# --- отчёт и скачивание ------------------------------------------------------------


def test_rent_retrain_report_uses_monthly_units():
    old = {"deal": "arenda", "metrics": {"model": {"mae": 40_000, "mape": 0.115, "r2": 0.86}}}
    new = {"deal": "arenda", "metrics": {"model": {"mae": 42_000, "mape": 0.11, "r2": 0.87},
                                         "n_train": 39_000, "n_test": 4_900}}
    text = monitoring.format_retrain_report(old, new, gate_passed=True,
                                            history=[{"mae": 40_000}, {"mae": 42_000}],
                                            dataset={"total": 1})
    assert "Модель аренды обновлена" in text
    assert "42.00 тыс. ₸/мес" in text
    assert "Тренд MAE (тыс. ₸/мес): 40.00 → 42.00" in text
    assert "млн" not in text


def test_ensure_rent_db_downloads_rent_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("KRISHA_DB_AUTO", "1")
    monkeypatch.setattr(db_release, "RENT_DB_PATH", tmp_path / "krisha_rent.db")
    calls = []
    monkeypatch.setattr(db_release, "download", lambda *a: calls.append(a) or True)
    assert db_release.ensure_rent_db() is True
    assert calls == [(tmp_path / "krisha_rent.db", "krisha_rent.db.gz", "KRISHA_RENT_DB_URL")]


def test_ensure_rent_models_downloads_rent_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("KRISHA_MODEL_AUTO", "1")
    monkeypatch.setattr(db_release, "RENT_MODEL_PATH", tmp_path / "rent" / "model.cbm")
    calls = []
    monkeypatch.setattr(db_release, "download_models", lambda *a: calls.append(a) or True)
    assert db_release.ensure_rent_models() is True
    assert calls == [(db_release.RENT_MODELS_DIR, "models_rent.tar.gz", "KRISHA_RENT_MODEL_URL")]
