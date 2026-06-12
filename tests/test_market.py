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


def _listing(lid: int, price: int) -> dict:
    return {"id": lid, "url": f"https://krisha.kz/a/show/{lid}", "price": price}


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
