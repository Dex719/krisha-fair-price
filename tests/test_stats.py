"""Тесты статистики рынка."""

from krisha import db, stats


def _make_db(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    rows = [
        {"id": i, "url": f"u{i}", "price": 50_000_000 + i * 1_000_000, "rooms": 1 + i % 3,
         "area": 50.0 + i, "district": "Bostandykskiy_r-n" if i % 2 else "Alatauskiy_r-n",
         "category": "vtorichka" if i % 2 else "novostroiki"}
        for i in range(10)
    ]
    for r in rows:
        listing = {c: r.get(c) for c in db.LISTING_COLUMNS}
        db.upsert_listing(listing, path)
    return path


def test_compute_stats(tmp_path):
    path = _make_db(tmp_path)
    s = stats.compute_stats(path)
    assert s["total_listings"] == 10
    assert s["median_price"] > 0
    assert {d["district"] for d in s["by_district"]} == {"Бостандыкский", "Алатауский"}
    assert sum(b["count"] for b in s["price_hist"]) == 10
    assert s["by_category"]["novostroiki"] == 5
    assert s["source"] == "db"


def test_compute_stats_missing_db(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        stats.compute_stats(tmp_path / "nope.db")
