"""Регрессия: схема ответа API не должна молча терять поля predict_from_listing.

Pydantic по умолчанию игнорирует лишние kwargs — из-за этого блоки «О доме»,
«Локация» и «Рынок» (этапы 2–4) не доходили до фронта. Этот тест ловит такое:
каждый ключ из результата предикта обязан быть объявлен в PredictResponse.
"""

from krisha.api.schemas import PredictResponse

# Ключи, которые возвращает predict.predict_from_listing (держим список в тесте
# явным, чтобы diff был виден при добавлении нового блока).
PREDICT_RESULT_KEYS = {
    "listing_id",
    "url",
    "title",
    "address",
    "actual_price",
    "fair_price",
    "verdict",
    "diff_pct",
    "top_factors",
    "details",
    "complex_details",
    "location_details",
    "price_history",
    "days_on_market",
    "liquidity",
    "text_flags",
    "photos",
    "description",
}


def test_predict_response_declares_all_result_keys():
    declared = set(PredictResponse.model_fields)
    missing = PREDICT_RESULT_KEYS - declared
    assert not missing, (
        f"PredictResponse не объявляет поля {sorted(missing)} — "
        "FastAPI молча вырежет их из ответа API"
    )


def test_predict_response_roundtrip_keeps_blocks():
    resp = PredictResponse(
        listing_id=1,
        url="https://krisha.kz/a/show/1",
        title="t",
        address="a",
        actual_price=50_000_000,
        fair_price=48_000_000.0,
        verdict="FAIR",
        diff_pct=4.2,
        top_factors=[],
        complex_details=[{"label": "Класс жилья", "value": "комфорт"}],
        location_details=[{"label": "Пешая доступность", "value": "73 / 100"}],
        price_history=[{"price": 50_000_000, "observed_at": "2026-06-01 00:00:00"}],
        days_on_market=12,
        liquidity={"median_days": 34, "sample": 21},
    )
    data = resp.model_dump()
    assert data["complex_details"][0]["value"] == "комфорт"
    assert data["location_details"]
    assert data["price_history"][0]["price"] == 50_000_000
    assert data["liquidity"]["median_days"] == 34
