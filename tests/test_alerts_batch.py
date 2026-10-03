"""Пакетная оценка алертов (.kiro/specs/rescrape-post-steps).

Поштучная карточка на каждый лот окна шла ~0.7 с на лот; после фикса SafeLine
окно выросло до 4–7 тыс. лотов, шаг Alerts — до 80 минут, и 2026-09-20 он не
дал ночному проходу залить базу. Пакет обязан давать ТЕ ЖЕ цифры, что карточка:
иначе в канал уйдут другие «выгодные» лоты.
"""

from pathlib import Path

import pytest

from krisha import alerts
from krisha.db import get_conn, init_db

FIXTURE = Path(__file__).parent / "fixtures" / "detail_sample.html"
FIELDS = ("fair_price", "fair_price_low", "fair_price_high", "verdict", "diff_pct")


def _real_listings() -> list[dict]:
    """Реальное объявление из фикстуры + варианты, которые меняют ветки фичей."""
    from krisha.scraping.detail_parser import parse_detail

    base = parse_detail(FIXTURE.read_text(encoding="utf-8"), "https://krisha.kz/a/show/1")
    assert base is not None, "фикстура должна разбираться парсером"
    return [
        {**base, "id": 1},
        # без цены — вердикта нет. Именно 0, а не None: build_features считает
        # log1p(price) и на None падает и в карточке, а окно алертов берёт
        # только price > 0 — ветку «вердикта нет» проверяем нулём.
        {**base, "id": 2, "price": 0},
        {**base, "id": 3, "lat": None, "lon": None},            # без координат — гео-фичи NaN
        {**base, "id": 4, "category": "novostroiki", "year_built": 2027, "rooms": 1, "area": 38.0},
        {**base, "id": 5, "price": (base.get("price") or 1) * 0.5},  # сильно дешевле
        {**base, "id": 6, "district": None, "microdistrict": None, "complex_name": None},
    ]


@pytest.mark.needs_model
def test_batch_rows_do_not_depend_on_their_neighbours():
    """Главный риск пакета: фичи пачки из N строк против N пачек по одной.

    build_features построчный, а категориальные фичи — строки, поэтому
    значения обязаны совпасть; тест ловит, если кто-то добавит межстрочную
    обработку или числовую категорию (2 → «2.0» в смешанной колонке)."""
    from krisha.predict import predict_listings_batch

    listings = _real_listings()
    together = predict_listings_batch(listings)
    one_by_one = [predict_listings_batch([listing])[0] for listing in listings]

    assert together == one_by_one


@pytest.mark.needs_model
def test_batch_matches_the_card(tmp_path, monkeypatch):
    """AC-4.1: пакет даёт те же цифры и вердикт, что пользовательская карточка."""
    from krisha import factor_hints
    from krisha import predict as predict_mod

    db = tmp_path / "t.db"
    init_db(db)
    monkeypatch.setattr(predict_mod, "DB_PATH", db)
    # Карточка строит подсказки к факторам по своей базе — туда же, чтобы тест
    # не зависел от локальной data/krisha.db и ничего не создавал рядом с ней.
    monkeypatch.setattr(factor_hints, "DB_PATH", db)

    listings = _real_listings()
    batch = predict_mod.predict_listings_batch(listings)
    for listing, row in zip(listings, batch, strict=True):
        card = predict_mod.predict_from_listing(dict(listing), live_vision=False)
        assert row["listing_id"] == listing["id"]
        for field in FIELDS:
            assert row[field] == card[field], f"лот {listing['id']}: {field}"


def _seed_window(db: Path, ids: list[int]) -> None:
    init_db(db)
    with get_conn(db) as conn:
        for i in ids:
            conn.execute(
                "INSERT INTO listings (id, url, price, area, is_active, first_seen, scraped_at) "
                "VALUES (?, ?, 30000000, 60.0, 1, datetime('now'), datetime('now'))",
                (i, f"https://krisha.kz/a/show/{i}"),
            )


def _fake_batch(verdicts: dict[int, str], calls: list[list[int]], broken: int | None = None):
    def fake(listings):
        ids = [listing["id"] for listing in listings]
        calls.append(ids)
        if broken in ids:
            raise ValueError("кривой лот")
        return [
            {"listing_id": i, "fair_price": 35_000_000.0, "fair_price_low": 33_000_000.0,
             "fair_price_high": 37_000_000.0, "verdict": verdicts[i],
             "diff_pct": -float(i), "model_version": "t"}
            for i in ids
        ]
    return fake


def test_find_good_deals_prices_in_batches_and_logs_every_listing(tmp_path, monkeypatch):
    """AC-3.1 и AC-5.1: карточка не вызывается, модель — по разу на порцию,
    в predictions — ровно по строке на лот окна."""
    from krisha import predict as predict_mod

    db = tmp_path / "t.db"
    _seed_window(db, [1, 2, 3])
    monkeypatch.setattr(alerts, "ALERTED_PATH", tmp_path / "alerted.json")
    monkeypatch.setattr(alerts, "ALERT_BATCH_SIZE", 2)
    calls: list[list[int]] = []
    monkeypatch.setattr(
        predict_mod, "predict_listings_batch",
        _fake_batch({1: "GOOD_DEAL", 2: "FAIR", 3: "GOOD_DEAL"}, calls),
    )

    def card_must_not_run(*a, **k):
        raise AssertionError("алерты не должны звать полную карточку")

    monkeypatch.setattr(predict_mod, "predict_from_listing", card_must_not_run)

    deals = alerts.find_good_deals(db)

    assert sorted(len(c) for c in calls) == [1, 2], "две порции: 2 + 1"
    assert [d["id"] for d in deals] == [3, 1], "только GOOD_DEAL, большая скидка первой"
    with get_conn(db) as conn:
        rows = conn.execute("SELECT listing_id, verdict FROM predictions ORDER BY listing_id").fetchall()
    assert [(r["listing_id"], r["verdict"]) for r in rows] == [
        (1, "GOOD_DEAL"), (2, "FAIR"), (3, "GOOD_DEAL"),
    ]


def test_one_broken_listing_does_not_sink_its_batch(tmp_path, monkeypatch):
    """Упала пачка — переоцениваем по одному; кривой лот пропускается, как раньше."""
    from krisha import predict as predict_mod

    db = tmp_path / "t.db"
    _seed_window(db, [1, 2, 3])
    monkeypatch.setattr(alerts, "ALERTED_PATH", tmp_path / "alerted.json")
    calls: list[list[int]] = []
    monkeypatch.setattr(
        predict_mod, "predict_listings_batch",
        _fake_batch({1: "GOOD_DEAL", 2: "GOOD_DEAL", 3: "GOOD_DEAL"}, calls, broken=2),
    )

    deals = alerts.find_good_deals(db)

    assert [d["id"] for d in deals] == [3, 1]
    with get_conn(db) as conn:
        logged = [r[0] for r in conn.execute("SELECT listing_id FROM predictions ORDER BY 1")]
    assert logged == [1, 3]


def test_suspiciously_cheap_lots_go_last_and_are_marked(tmp_path, monkeypatch):
    """Свежий лот глубоко ниже нижней границы — карточка пишет про него
    «цена сильно ниже рынка, проверьте». В рассылке он не может идти первым
    «лучшим предложением» только потому, что скидка самая большая."""
    from krisha import predict as predict_mod

    db = tmp_path / "t.db"
    _seed_window(db, [1, 2])  # цена у обоих 30 млн
    monkeypatch.setattr(alerts, "ALERTED_PATH", tmp_path / "alerted.json")
    lows = {1: 31_000_000.0, 2: 40_000_000.0}  # ниже границы на 3% и на 25%
    monkeypatch.setattr(predict_mod, "predict_listings_batch", lambda listings: [
        {"listing_id": x["id"], "fair_price": lows[x["id"]] * 1.1, "fair_price_low": lows[x["id"]],
         "fair_price_high": lows[x["id"]] * 1.2, "verdict": "GOOD_DEAL",
         "diff_pct": -5.0 if x["id"] == 1 else -32.0, "model_version": "t"}
        for x in listings
    ])

    deals = alerts.find_good_deals(db)

    assert [d["id"] for d in deals] == [1, 2], "подозрительный — после обычного"
    assert [d["suspicious"] for d in deals] == [False, True]
    text = alerts.format_alert(deals)
    assert text.count("Цена сильно ниже рынка") == 1 and "на 25% ниже нижней границы" in text


def test_whole_window_failing_is_an_error_not_zero_deals(tmp_path, monkeypatch):
    """Нет модели — это поломка, а не «выгодных нет»: иначе рассылка и канал
    молча пустеют, а ран зелёный (так было 01.10–03.10)."""
    from krisha import predict as predict_mod

    db = tmp_path / "t.db"
    _seed_window(db, [1, 2, 3])
    monkeypatch.setattr(alerts, "ALERTED_PATH", tmp_path / "alerted.json")

    def no_model(chunk):
        raise FileNotFoundError("Модель не найдена")

    monkeypatch.setattr(predict_mod, "predict_listings_batch", no_model)

    with pytest.raises(alerts.AlertsPricingFailed, match="3 лотов"):
        alerts.find_good_deals(db)


def test_empty_window_is_not_an_error(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    _seed_window(db, [])
    monkeypatch.setattr(alerts, "ALERTED_PATH", tmp_path / "alerted.json")

    assert alerts.find_good_deals(db) == []


@pytest.mark.parametrize("rows, expected", [([], 0), ([{"listing_id": None}], 0)])
def test_log_predictions_skips_empty_input(tmp_path, rows, expected):
    from krisha.db import log_predictions

    db = tmp_path / "t.db"
    init_db(db)
    assert log_predictions(rows, db_path=db) == expected


def test_window_skips_old_lots_whose_details_just_arrived(tmp_path, monkeypatch):
    """Докачка деталей двигает scraped_at у давно висящих лотов. Они не новые
    для подписчика, а «подозрительно дёшево» для них не включается."""
    db = tmp_path / "t.db"
    _seed_window(db, [1, 2, 3])
    with get_conn(db) as conn:
        # 2 — деталь пришла сегодня, но на рынке он 30 дней; 3 — 3 дня
        conn.execute("UPDATE listings SET first_seen = datetime('now', '-30 days') WHERE id = 2")
        conn.execute("UPDATE listings SET first_seen = datetime('now', '-3 days') WHERE id = 3")
    monkeypatch.setattr(alerts, "ALERTED_PATH", tmp_path / "alerted.json")

    ids = sorted(row["id"] for row in alerts.new_listings(db))

    assert ids == [1, 3]
