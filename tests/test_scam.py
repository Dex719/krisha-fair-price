"""Тесты скам-детектора: пороги, эвристики, фильтр ложных тревог."""

from krisha.scam import assess_scam_risk


def test_high_risk_cheap_no_photos_prepay():
    """Дёшево + нет фото + задаток в описании = high."""
    listing = {"photos": [], "description": "Срочно! Задаток на Каспи, уезжаю завтра"}
    risk = assess_scam_risk(listing, fair_price=50_000_000, actual_price=32_000_000)
    assert risk is not None and risk["level"] == "high"
    assert any("ниже оценки" in r for r in risk["reasons"])
    assert any("задаток" in r for r in risk["reasons"])
    assert any("фотографий" in r for r in risk["reasons"])


def test_medium_risk_only_price_and_short_desc():
    """Цена -25% и короткое описание = medium."""
    listing = {"photos": ["a.jpg"] * 8, "description": "Продам"}
    risk = assess_scam_risk(listing, fair_price=50_000_000, actual_price=37_000_000)
    assert risk is not None and risk["level"] == "medium"


def test_fair_price_listing_not_flagged():
    """Обычное объявление по рынку не пугает, даже если лаконичное."""
    listing = {"photos": [], "description": ""}
    assert assess_scam_risk(listing, fair_price=50_000_000, actual_price=49_000_000) is None


def test_good_deal_with_normal_listing_not_flagged():
    """Просто выгодный лот (-13%) с фото и описанием — не скам."""
    listing = {"photos": ["a"] * 10, "description": "Хорошая квартира, " * 10}
    assert assess_scam_risk(listing, fair_price=50_000_000, actual_price=43_000_000) is None


def test_no_price_or_fair_returns_none():
    assert assess_scam_risk({}, fair_price=50_000_000, actual_price=None) is None
    assert assess_scam_risk({}, fair_price=0, actual_price=1) is None
