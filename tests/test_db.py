from krisha.db import count_listings, init_db, known_ids, upsert_listing


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
    upsert_listing(make_listing(1, price=10), db)
    upsert_listing(make_listing(1, price=20), db)
    assert count_listings(db) == 1
    from krisha.db import get_conn
    with get_conn(db) as conn:
        assert conn.execute("SELECT price FROM listings WHERE id=1").fetchone()[0] == 20
