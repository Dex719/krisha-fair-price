"""Оценка арендных объявлений: тип сделки, своя модель и база, подписи."""

import dataclasses
import json

import pytest

from krisha import bot
from krisha import predict as predict_mod
from krisha.api.schemas import PredictResponse
from krisha.db import get_conn, init_db
from krisha.model_spec import RENT
from krisha.rent import detect_deal
from krisha.spatial import load_spatial_ref
from krisha.train import train
from tests.test_rent_model import RENT_PARAMS, SALE_PARAMS, synthetic_rent_df

# --- тип сделки ----------------------------------------------------------------


@pytest.mark.parametrize("listing, deal", [
    ({"deal": "arenda", "price": 90_000_000}, "arenda"),               # явный sectionAlias главнее
    ({"price": 350_000, "raw_params": "{}"}, "arenda"),                 # аренда по цене
    ({"price": 45_000_000, "raw_params": json.dumps(RENT_PARAMS)}, "arenda"),
    ({"price": 45_000_000, "raw_params": json.dumps(SALE_PARAMS)}, "prodazha"),
    # старый формат арендной страницы с продажным ключом — цена решает
    ({"price": 400_000, "raw_params": json.dumps({"flat.renovation": "свежий"})}, "arenda"),
    ({"price": 4_000_000, "raw_params": "{}"}, "arenda"),
    ({"price": 30_000_000, "raw_params": "{}"}, "prodazha"),
    ({"price": None, "raw_params": None}, "prodazha"),                  # нечем решить — как раньше
])
def test_detect_deal(listing, deal):
    assert detect_deal(listing) == deal


def test_parser_reads_section_alias():
    from pathlib import Path

    from krisha.scraping.detail_parser import parse_detail

    html = (Path(__file__).parent / "fixtures" / "detail_sample.html").read_text(encoding="utf-8")
    assert parse_detail(html, "https://krisha.kz/a/show/1")["deal"] == "prodazha"


# --- предикт аренды ----------------------------------------------------------------


@pytest.fixture(scope="module")
def rent_model(tmp_path_factory):
    """Крошечная модель аренды на синтетике — CI не скачивает веса из релиза."""
    d = tmp_path_factory.mktemp("rent_model")
    spec = dataclasses.replace(
        RENT,
        models_dir=d,
        model_path=d / "model.cbm",
        quantile_path=d / "model_quantile.cbm",
        meta_path=d / "model_meta.json",
        spatial_ref_path=d / "spatial_ref.json",
        gate_samples_path=d / "model_gate_samples.json",
        metrics_history_path=d / "metrics_history.jsonl",
        shap_path=d / "shap.png",
    )
    mp = pytest.MonkeyPatch()
    mp.setattr("krisha.zones.load_zone_index", lambda *a, **k: None)
    mp.setattr("krisha.train._save_shap_report", lambda *a, **k: None)
    train(df=synthetic_rent_df(), iterations=60, save=True, spec=spec)
    mp.undo()
    return spec


@pytest.fixture()
def rent_env(rent_model, tmp_path, monkeypatch):
    monkeypatch.setattr(predict_mod, "RENT_MODEL_PATH", rent_model.model_path)
    monkeypatch.setattr(predict_mod, "RENT_MODEL_META_PATH", rent_model.meta_path)
    monkeypatch.setattr(predict_mod, "RENT_MODEL_QUANTILE_PATH", rent_model.quantile_path)
    monkeypatch.setattr(predict_mod, "RENT_SPATIAL_REF_PATH", rent_model.spatial_ref_path)
    for cached in (predict_mod.load_model, predict_mod.load_interval_models, load_spatial_ref):
        cached.cache_clear()
    db = tmp_path / "krisha_rent.db"
    init_db(db)
    yield db
    for cached in (predict_mod.load_model, predict_mod.load_interval_models, load_spatial_ref):
        cached.cache_clear()


def _rent_listing(**overrides):
    return {
        "id": 777, "url": "https://krisha.kz/a/show/777", "title": "2-комнатная квартира · 55 м²",
        "price": 300_000, "rooms": 2, "area": 55.0, "floor": 5, "total_floors": 10,
        "district": "Bostandykskiy_r-n", "lat": 43.23, "lon": 76.9, "user_type": "owner",
        "category": "kvartiry", "photos_count": 10,
        "raw_params": json.dumps(RENT_PARAMS, ensure_ascii=False),
        "description": "Сдаётся уютная квартира на длительный срок",
        **overrides,
    }


def test_rent_listing_uses_rent_model_and_monthly_prices(rent_env):
    with get_conn(rent_env) as conn:
        r = predict_mod.predict_from_listing(_rent_listing(), live_vision=False, conn=conn)
    assert r["deal"] == "arenda" and r["price_period"] == "month"
    assert 50_000 < r["fair_price"] < 2_000_000, "оценка в ₸/мес, а не в миллионах"
    assert r["fair_price"] % 1_000 == 0  # аренду округляем до тысячи, не до 10 тыс.
    assert r["verdict"] in ("GOOD_DEAL", "FAIR", "OVERPRICED")
    labels = {d["label"]: d["value"] for d in r["details"]}
    assert labels["Арендодатель"] == "Собственник"
    assert labels["Кому сдаётся"] == RENT_PARAMS["who_match"]
    assert "Категория" not in labels  # у аренды «kvartiry» ничего не говорит
    assert all("hint" not in f for f in r["top_factors"]), "продажные подсказки аренде врут"
    PredictResponse(**r)  # ответ валиден для API
    with get_conn(rent_env) as conn:
        version = conn.execute("select model_version from predictions").fetchone()[0]
    assert version.startswith("arenda:"), "лог предиктов общий — версия помечена сделкой"


def test_room_share_gets_no_verdict(rent_env):
    listing = _rent_listing(description="Ищу девушку на подселение, место в комнате", price=90_000)
    with get_conn(rent_env) as conn:
        r = predict_mod.predict_from_listing(listing, live_vision=False, conn=conn)
    assert r["room_share"] is True
    assert r["verdict"] is None
    assert r["fair_price"] > 90_000  # оценка — за квартиру целиком


# --- бот ---------------------------------------------------------------------------


def test_bot_reply_for_rent_uses_monthly_units():
    result = {
        "deal": "arenda", "title": "2-комнатная квартира", "address": "Абая 10",
        "actual_price": 320_000, "fair_price": 290_000, "verdict": "OVERPRICED", "diff_pct": 10.3,
        "liquidity": {"median_days": 9, "sample": 120},
        "top_factors": [{"feature": "fac_aircon", "impact": 0.05, "impact_pct": 5.1, "impact_tenge": 14_000}],
        "analogs": [{"url": "https://krisha.kz/a/show/1", "title": "2-комн", "price": 280_000}],
    }
    text = bot.format_reply(result)
    assert "Аренда в объявлении: <b>320 000 ₸/мес</b>" in text
    assert "Справедливая аренда: <b>290 000 ₸/мес</b>" in text
    assert "сдают за" in text and "снимают с продажи" not in text
    assert "Кондиционер: +5.1% (+14 тыс ₸/мес)" in text
    assert "280 тыс ₸/мес" in text
    assert "млн" not in text


def test_bot_reply_for_room_share_warns():
    text = bot.format_reply({"deal": "arenda", "actual_price": 90_000, "fair_price": 300_000,
                             "verdict": None, "room_share": True})
    assert "подселение" in text
