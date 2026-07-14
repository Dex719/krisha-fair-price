from krisha.db import (
    count_listings,
    get_conn,
    get_price_history,
    init_db,
    known_ids,
    log_prediction,
    record_sighting,
    upsert_listing,
)


def make_listing(lid=1, price=42_000_000):
    return {"id": lid, "url": f"https://krisha.kz/a/show/{lid}", "price": price,
            "title": "test", "rooms": 2, "area": 60.0}


def test_init_and_upsert(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    assert count_listings(db) == 0

    upsert_listing(make_listing(1), db)
    upsert_listing(make_listing(2), db)
    assert count_listings(db) == 2
    assert known_ids(db) == {1, 2}


def test_upsert_updates_price(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    # issue #103: цены проверяются на PRICE_MIN..PRICE_MAX при upsert — берём
    # реалистичные значения, а не произвольные заглушки 10/20 (те теперь
    # попадают в карантин как аномалия и не меняют price, см. test_db_anomalies.py).
    upsert_listing(make_listing(1, price=30_000_000), db)
    upsert_listing(make_listing(1, price=35_000_000), db)
    assert count_listings(db) == 1
    with get_conn(db) as conn:
        assert conn.execute("SELECT price FROM listings WHERE id=1").fetchone()[0] == 35_000_000


# ---------- issue #117: user-предикт не должен затирать скрейп-данные ----------


def test_user_upsert_does_not_overwrite_scraped_price_and_title(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(make_listing(1, price=50_000_000), db)  # свежий скрейп

    stale = make_listing(1, price=10_000_000)  # устаревшая закэшированная страница
    stale["title"] = "старый заголовок"
    stale["source"] = "user"
    upsert_listing(stale, db)

    with get_conn(db) as conn:
        row = conn.execute("SELECT price, title FROM listings WHERE id=1").fetchone()
    assert row[0] == 50_000_000  # цена НЕ затёрта user-предиктом
    assert row[1] == "test"  # title НЕ затёрт
    # и точка в price_history не появилась из user-пути
    assert [p["price"] for p in get_price_history(1, db)] == [50_000_000]


def test_user_upsert_inserts_new_listing_fully(tmp_path):
    """Первое появление лота (в т.ч. через user-путь) — данные пишутся как обычно."""
    db = tmp_path / "test.db"
    init_db(db)
    listing = make_listing(9, price=30_000_000)
    listing["source"] = "user"
    upsert_listing(listing, db)

    with get_conn(db) as conn:
        row = conn.execute("SELECT price, title, source FROM listings WHERE id=9").fetchone()
    assert row[0] == 30_000_000
    assert row[1] == "test"
    assert row[2] == "user"
    # но price_history из user-пути всё равно не пишется (issue #117)
    assert get_price_history(9, db) == []


def test_user_upsert_fills_missing_fields_but_not_price(tmp_path):
    """COALESCE по остальным полям всё же подтягивает свежие данные (напр. area)."""
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(make_listing(1, price=50_000_000), db)

    fresh = make_listing(1, price=999_000_000)  # цену игнорируем
    fresh["source"] = "user"
    fresh["area"] = 75.0
    upsert_listing(fresh, db)

    with get_conn(db) as conn:
        row = conn.execute("SELECT price, area FROM listings WHERE id=1").fetchone()
    assert row[0] == 50_000_000
    assert row[1] == 75.0


# ---------- issue #127: sighting — лёгкая запись для всех найденных id ----------


def test_record_sighting_creates_row_without_detail(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    record_sighting(42, "https://krisha.kz/a/show/42", 20_000_000, db)

    with get_conn(db) as conn:
        row = conn.execute(
            "SELECT title, price, first_seen, is_active FROM listings WHERE id=42"
        ).fetchone()
    assert row["title"] is None  # сентинел «нет детали ещё»
    assert row["price"] == 20_000_000
    assert row["first_seen"] is not None
    assert row["is_active"] == 1
    assert [p["price"] for p in get_price_history(42, db)] == [20_000_000]


def test_record_sighting_then_full_upsert_fills_detail(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    record_sighting(42, "https://krisha.kz/a/show/42", 20_000_000, db)
    upsert_listing(make_listing(42, price=21_000_000), db)

    with get_conn(db) as conn:
        row = conn.execute("SELECT title, price FROM listings WHERE id=42").fetchone()
    assert row["title"] == "test"
    assert row["price"] == 21_000_000


# ---------- issue #128: лог предиктов ----------


def test_log_prediction_writes_row(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    log_prediction(1, 45_000_000.0, 40_000_000.0, 50_000_000.0, "FAIR", "2026-07-01T00:00:00", db)

    with get_conn(db) as conn:
        row = conn.execute(
            "SELECT listing_id, fair_price, fair_low, fair_high, verdict, model_version "
            "FROM predictions"
        ).fetchone()
    assert tuple(row) == (1, 45_000_000.0, 40_000_000.0, 50_000_000.0, "FAIR", "2026-07-01T00:00:00")


def test_log_prediction_noop_without_listing_id(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    log_prediction(None, 1.0, None, None, None, None, db)
    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
