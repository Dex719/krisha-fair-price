"""Тесты оценки по вставленному тексту (Gemini-извлечение параметров)."""

from krisha import text_parse
from krisha.text_parse import parsed_to_listing, predict_from_text

TEXT = ("Продам уютную 2-комнатную квартиру 60 м² в Бостандыкском районе, "
        "5/9 этаж, кирпичный дом 2015 года, 45 млн тенге, торг.")

PARSED = {"is_listing": True, "rooms": 2, "area": 60.0, "floor": 5,
          "total_floors": 9, "year_built": 2015, "district": "Бостандыкский",
          "building_type": "кирпичный", "price": 45_000_000}


def test_parsed_to_listing_maps_district_slug():
    listing = parsed_to_listing(PARSED, TEXT)
    assert listing["district"] == "Bostandykskiy_r-n"
    assert listing["price"] == 45_000_000 and listing["rooms"] == 2
    assert listing["description"] == TEXT
    assert "microdistrict" not in listing  # null-поля выброшены, price остаётся

    no_price = parsed_to_listing({**PARSED, "price": None, "district": None}, TEXT)
    assert no_price["price"] is None and "district" not in no_price


def test_predict_from_text_soft_paths(monkeypatch):
    # без ключа — None (бот покажет обычную подсказку)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert predict_from_text(TEXT) is None

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    # короткий текст — None
    assert predict_from_text("привет") is None
    # не объявление — None
    monkeypatch.setattr(text_parse, "_gemini_extract",
                        lambda text, key: {"is_listing": False})
    assert predict_from_text(TEXT) is None
    # объявление без площади/комнат — мягкая ошибка
    monkeypatch.setattr(text_parse, "_gemini_extract",
                        lambda text, key: {"is_listing": True, "rooms": None, "area": None})
    assert predict_from_text(TEXT)["error"] == "no_key_fields"


def test_predict_from_text_happy_path(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(text_parse, "_gemini_extract", lambda text, key: dict(PARSED))
    captured = {}

    def fake_predict(listing, flags_live=True):
        captured.update(listing)
        return {"fair_price": 48_000_000, "verdict": "FAIR", "actual_price": listing.get("price")}

    monkeypatch.setattr("krisha.predict.predict_from_listing", fake_predict)
    result = predict_from_text(TEXT)
    assert result["from_text"] is True
    assert result["fair_price"] == 48_000_000
    assert captured["district"] == "Bostandykskiy_r-n" and captured["area"] == 60.0
    assert result["parsed_fields"]["rooms"] == 2
    assert "description" not in result["parsed_fields"]
