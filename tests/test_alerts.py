"""Тесты алертов: разбор фильтров, матчинг, формат сообщения, тренд."""

from krisha import db
from krisha.alerts import format_alert, match_filters
from krisha.stats import compute_stats
from krisha.subscriptions import describe_filters, parse_filters


def test_parse_filters_full():
    flt = parse_filters("2к до 45млн бостандыкский")
    assert flt == {"rooms": 2, "max_price": 45_000_000, "district": "Bostandykskiy_r-n"}


def test_parse_filters_variants():
    assert parse_filters("")["rooms"] is None
    assert parse_filters("3 60")["rooms"] == 3
    assert parse_filters("3 60")["max_price"] == 60_000_000
    assert parse_filters("медеу")["district"] == "Medeuskiy_r-n"
    assert parse_filters("120.5млн")["max_price"] == 120_500_000


def test_match_filters():
    listing = {"rooms": 2, "price": 40_000_000, "district": "Bostandykskiy_r-n"}
    assert match_filters(listing, {"rooms": 2, "max_price": 45_000_000, "district": "Bostandykskiy_r-n"})
    assert not match_filters(listing, {"rooms": 3, "max_price": None, "district": None})
    assert not match_filters(listing, {"rooms": None, "max_price": 39_000_000, "district": None})
    assert match_filters(listing, {})  # без фильтров проходит всё


def test_format_alert_and_describe():
    deals = [{
        "url": "https://krisha.kz/a/show/1", "title": "2-комнатная, 60 м²",
        "price": 40_000_000, "fair_price": 47_000_000, "diff_pct": -14.9,
        "district": "Bostandykskiy_r-n",
    }]
    text = format_alert(deals)
    assert "krisha.kz/a/show/1" in text and "40.0 млн" in text and "-14.9%" in text
    assert "Бостандыкский" in text and "/alerts_off" in text
    assert "45 млн" in describe_filters(parse_filters("2к 45"))


def test_stats_trend_key(tmp_path):
    # Строим временную базу — в CI и на проде data/krisha.db в git больше нет.
    path = tmp_path / "t.db"
    db.init_db(path)
    for i in range(10):
        listing = {c: None for c in db.LISTING_COLUMNS}
        listing.update(
            {"id": i, "url": f"u{i}", "price": 50_000_000 + i * 1_000_000, "rooms": 2, "area": 60.0}
        )
        db.upsert_listing(listing, path)
    stats = compute_stats(path)
    assert "trend" in stats
    for point in stats["trend"]:
        assert set(point) == {"week", "median_ppsm", "n"}
        assert point["median_ppsm"] > 100_000
