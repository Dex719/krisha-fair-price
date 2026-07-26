"""Тесты этапа 4: история цены, дни на рынке, парсер цен выдачи."""

import sqlite3

from krisha.db import (
    _record_price_if_changed,
    get_conn,
    get_price_history,
    init_db,
    upsert_listing,
)
from krisha.scraping.listing_parser import parse_listing_prices

CARD = """
<a href="/a/show/111"></a><a href="/a/show/222"></a><a href="/a/show/333"></a>
<div data-id="111" class="a-card">
  <div class="a-card__price"> 45&nbsp;000&nbsp;000 </div>
</div>
<div data-id="222" class="a-card">
  <div class="a-card__price">от 94&nbsp;930&nbsp;000 </div>
</div>
<div data-id="333" class="a-card"><span>без цены</span></div>
"""


def test_parse_listing_prices():
    prices = parse_listing_prices(CARD)
    assert prices == {111: 45_000_000, 222: 94_930_000, 333: None}


def test_parse_listing_prices_promo_card_between_id_and_price():
    """issue #98: промо-блок (баннер, без своей цены) МЕЖДУ карточкой и её
    ценой раньше «съезжал» позиционный матчинг — цена доставалась соседу.
    Структурный DOM-парсинг ищет цену внутри своего узла .a-card, промо-блок
    вообще не задевает."""
    html = """
    <a href="/a/show/111"></a><a href="/a/show/222"></a>
    <div data-id="111" class="a-card">
      <div class="promo-banner">Реклама без цены и без .a-card__price</div>
      <div class="a-card__price">50&nbsp;000&nbsp;000</div>
    </div>
    <div data-id="222" class="a-card">
      <div class="a-card__price">60&nbsp;000&nbsp;000</div>
    </div>
    """
    assert parse_listing_prices(html) == {111: 50_000_000, 222: 60_000_000}


def test_parse_listing_prices_extra_data_id_on_nested_button():
    """issue #98: доп. data-id у кнопки «избранное» внутри карточки (позиционно
    между id карточки и её ценой) раньше сдвигал бы привязку — теперь цена
    ищется структурно внутри самого узла .a-card независимо от вложенных
    элементов с собственным data-id."""
    html = """
    <a href="/a/show/111"></a><a href="/a/show/999"></a>
    <div data-id="111" class="a-card">
      <button class="favorite-btn" data-id="999" aria-label="В избранное"></button>
      <div class="a-card__price">33&nbsp;000&nbsp;000</div>
    </div>
    """
    assert parse_listing_prices(html) == {111: 33_000_000}


def test_parse_listing_prices_reordered_blocks_dont_bleed():
    """issue #98: карточка с ценой ПЕРЕД блоком с id (нестандартный порядок
    внутри узла) — позиционный подход по всей странице был бы не при делах,
    структурный корректно находит цену внутри своего .a-card независимо от
    внутреннего порядка тегов."""
    html = """
    <a href="/a/show/111"></a>
    <div data-id="111" class="a-card">
      <div class="a-card__price">70&nbsp;000&nbsp;000</div>
      <div class="a-card__title">Заголовок после цены</div>
    </div>
    """
    assert parse_listing_prices(html) == {111: 70_000_000}


def _listing(lid: int, price: int) -> dict:
    return {"id": lid, "url": f"https://krisha.kz/a/show/{lid}", "price": price}


def test_record_price_jump_warns_but_still_records(tmp_path, caplog):
    """issue #98 (second-line defence): скачок цены >60% пишется в history
    (не блокируется), но логируется как подозрительный."""
    import logging
    import time

    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        assert _record_price_if_changed(conn, 1, 100_000_000)
        time.sleep(0.01)  # разные observed_at (PK price_history — listing_id+observed_at)
        with caplog.at_level(logging.WARNING):
            assert _record_price_if_changed(conn, 1, 30_000_000)  # -70%
        assert any("подозрительный скачок цены" in r.message for r in caplog.records)
    assert [p["price"] for p in get_price_history(1, db)] == [100_000_000, 30_000_000]


def test_record_price_small_change_no_warning(tmp_path, caplog):
    import logging

    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        assert _record_price_if_changed(conn, 1, 100_000_000)
        with caplog.at_level(logging.WARNING):
            assert _record_price_if_changed(conn, 1, 95_000_000)  # -5%
        assert not any("подозрительный скачок цены" in r.message for r in caplog.records)


def test_upsert_creates_history_and_tracks_changes(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(1, 30_000_000), db)
    assert [p["price"] for p in get_price_history(1, db)] == [30_000_000]

    # та же цена → новая точка не пишется
    upsert_listing(_listing(1, 30_000_000), db)
    assert len(get_price_history(1, db)) == 1

    # цена изменилась → новая точка
    with get_conn(db) as conn:
        assert _record_price_if_changed(conn, 1, 28_500_000)
        assert not _record_price_if_changed(conn, 1, 28_500_000)
    assert [p["price"] for p in get_price_history(1, db)] == [30_000_000, 28_500_000]


def test_migration_backfills_legacy_rows(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE listings (id INTEGER PRIMARY KEY, url TEXT, price INTEGER, "
        "district TEXT, rooms INTEGER, scraped_at TEXT DEFAULT (datetime('now')))"
    )
    conn.execute("INSERT INTO listings (id, url, price) VALUES (5, 'u', 50000000)")
    conn.commit()
    conn.close()

    init_db(db)  # миграция: новые колонки + бэкфилл first_seen и истории
    with get_conn(db) as c:
        row = c.execute(
            "SELECT first_seen, last_seen, is_active FROM listings WHERE id = 5"
        ).fetchone()
    assert row["first_seen"] is not None
    assert row["is_active"] == 1
    assert [p["price"] for p in get_price_history(5, db)] == [50_000_000]


def test_upsert_revives_delisted(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(7, 40_000_000), db)
    with get_conn(db) as conn:
        conn.execute(
            "UPDATE listings SET is_active = 0, delisted_at = datetime('now') WHERE id = 7"
        )
    upsert_listing(_listing(7, 40_000_000), db)
    with get_conn(db) as conn:
        row = conn.execute("SELECT is_active, delisted_at FROM listings WHERE id = 7").fetchone()
    assert row["is_active"] == 1 and row["delisted_at"] is None


# ---------- срок продажи v1: liquidity_estimate ----------

from datetime import datetime, timedelta  # noqa: E402

from krisha.market import liquidity_estimate  # noqa: E402

DISTRICT = "Bostandykskiy_r-n"


def _add_delisted(
    db,
    lid: int,
    *,
    district: str = DISTRICT,
    rooms: int = 2,
    price: int = 50_000_000,
    area: float = 50.0,
    first_seen: str = "2026-06-10 00:00:00",
    days: float = 10.0,
    active: bool = False,
) -> None:
    delisted = (
        datetime.fromisoformat(first_seen) + timedelta(days=days)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, rooms, area, district, first_seen, "
            "last_seen, is_active, delisted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lid,
                f"https://krisha.kz/a/show/{lid}",
                price,
                rooms,
                area,
                district,
                first_seen,
                delisted,
                0 if not active else 1,
                delisted if not active else None,
            ),
        )


def _anchor(db) -> None:
    """Первая строка базы: задаёт MIN(first_seen) = старт скрейпа."""
    _add_delisted(db, 999_999, first_seen="2026-06-01 00:00:00", days=1.0, active=True)


def test_liquidity_none_without_data(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    assert liquidity_estimate(DISTRICT, 2, db_path=db) is None
    assert liquidity_estimate(None, 2, db_path=db) is None
    assert liquidity_estimate(DISTRICT, None, db_path=db) is None


def test_liquidity_segment_median(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    _anchor(db)
    for i, days in enumerate(range(5, 20)):  # 15 снятых, медиана 12
        _add_delisted(db, 1000 + i, days=float(days))
    est = liquidity_estimate(DISTRICT, 2, db_path=db)
    assert est == {
        "median_days": 12,
        "sample": 15,
        "scope": "district_rooms",
        "band": None,
        "band_median_days": None,
        "band_sample": None,
    }


def test_liquidity_excludes_censored_and_bad_durations(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    _anchor(db)
    for i in range(14):  # на одну меньше порога
        _add_delisted(db, 1000 + i, days=10.0)
    # шум, который не должен пройти фильтры:
    _add_delisted(db, 2001, days=0.5)                                # < 1 дня
    _add_delisted(db, 2002, days=-3.0)                               # отрицательная
    _add_delisted(db, 2003, first_seen="2026-06-01 12:00:00", days=30.0)  # когорта старта
    _add_delisted(db, 2004, days=10.0, active=True)                  # активное
    # Лот 2005: снятие замечено через 12 дней после последнего наблюдения —
    # цензурированный эпизод (issue #156). Столько лот не наблюдался, момент
    # ухода с рынка неизвестен. Так выглядит вся волна после провала сбора.
    _add_delisted(db, 2006, days=10.0)
    with get_conn(db) as conn:
        conn.execute(
            "UPDATE listings SET delisted_at = datetime(last_seen, '+12 days') "
            "WHERE id = 2006"
        )
    # Метку 'initial' проставляет миграция; в проде init_db гоняется в начале
    # каждого прохода и при старте API, здесь воспроизводим тот же момент.
    init_db(db)
    assert liquidity_estimate(DISTRICT, 2, db_path=db) is None
    _add_delisted(db, 2005, days=10.0)  # 15-е валидное → оценка появляется
    est = liquidity_estimate(DISTRICT, 2, db_path=db)
    assert est is not None and est["sample"] == 15


def test_liquidity_city_fallback(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    _anchor(db)
    for i in range(30):  # 30 снятых в других районах
        _add_delisted(db, 3000 + i, district=f"r{i % 3}", rooms=1 + i % 3, days=20.0)
    est = liquidity_estimate(DISTRICT, 2, db_path=db)
    assert est is not None
    assert est["scope"] == "city" and est["sample"] == 30 and est["median_days"] == 20
    assert est["band"] is None  # ценовые полосы только внутри сегмента


def test_liquidity_price_bands(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    _anchor(db)
    # 60 снятых: дешёвые уходят за ~5 дн., в рынке ~10, дорогие ~30
    for i in range(20):
        _add_delisted(db, 4000 + i, price=40_000_000, days=5.0)   # ₸/м² −20%
        _add_delisted(db, 4100 + i, price=50_000_000, days=10.0)  # медиана
        _add_delisted(db, 4200 + i, price=60_000_000, days=30.0)  # ₸/м² +20%
    over = liquidity_estimate(DISTRICT, 2, diff_pct=12.0, db_path=db)
    assert over["band"] == "above"
    assert over["band_median_days"] == 30 and over["band_sample"] == 20
    under = liquidity_estimate(DISTRICT, 2, diff_pct=-12.0, db_path=db)
    assert under["band"] == "below" and under["band_median_days"] == 5
    fair = liquidity_estimate(DISTRICT, 2, diff_pct=0.0, db_path=db)
    assert fair["band"] == "near" and fair["band_median_days"] == 10
    assert fair["median_days"] == 10 and fair["sample"] == 60
    # без diff_pct полосы не считаем
    assert liquidity_estimate(DISTRICT, 2, db_path=db)["band"] is None
