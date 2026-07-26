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


def test_alert_window_catches_listing_detailed_after_first_sighting(tmp_path, monkeypatch):
    """Регрессия: issue #127 развёл sighting и докачку деталей по разным
    очередям. Пока деталей нет, у строки нет area, и фильтр `area > 0` её
    отсекает; когда детали наконец приезжают, first_seen уже вне окна — лот
    не попадал в алерты и дайджест НИКОГДА."""
    from krisha import alerts
    from krisha.db import get_conn, init_db

    db = tmp_path / "t.db"
    init_db(db)
    monkeypatch.setattr(alerts, "ALERTED_PATH", tmp_path / "alerted.json")
    with get_conn(db) as conn:
        # Замечен 5 дней назад (sighting), детали докачали только что.
        conn.execute(
            "INSERT INTO listings (id, url, price, area, is_active, first_seen, scraped_at) "
            "VALUES (1, 'u', 30000000, 60.0, 1, datetime('now','-5 days'), datetime('now'))"
        )
    found = [r["id"] for r in alerts.new_listings(db)]
    assert found == [1], "лот с поздней докачкой деталей должен попадать в окно"


def test_alerted_listings_are_not_resent(tmp_path, monkeypatch):
    """Обратная сторона: scraped_at обновляет и очередь дообновления
    устаревших деталей (issue #102), поэтому без отметки об отправке один и
    тот же лот уезжал бы подписчикам на каждом проходе."""
    from krisha import alerts
    from krisha.db import get_conn, init_db

    db = tmp_path / "t.db"
    init_db(db)
    path = tmp_path / "alerted.json"
    monkeypatch.setattr(alerts, "ALERTED_PATH", path)
    monkeypatch.setattr("krisha.subscriptions._push_to_github", lambda *a, **k: None)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, area, is_active, first_seen, scraped_at) "
            "VALUES (2, 'u', 30000000, 60.0, 1, datetime('now'), datetime('now'))"
        )
    assert [r["id"] for r in alerts.new_listings(db)] == [2]

    sent = []
    monkeypatch.setattr(
        "krisha.bot.tg_call", lambda m, **kw: sent.append(kw) or {"ok": True}
    )
    deals = [{"id": 2, "url": "u", "price": 30_000_000, "area": 60.0,
              "fair_price": 35_000_000, "diff_pct": -14.3, "title": "Лот"}]
    assert alerts.send_alerts({"77": {}}, deals) == 1
    assert alerts.load_alerted(path) == [2]
    assert alerts.new_listings(db) == [], "повторно тот же лот слать нельзя"
