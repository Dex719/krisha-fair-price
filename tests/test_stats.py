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
    assert len(s["ppsm_hist"]) == stats.PPSM_HIST_BINS
    assert sum(b["count"] for b in s["ppsm_hist"]) == 10
    assert {"label", "count", "from_ppsm", "to_ppsm"} <= set(s["ppsm_hist"][0])
    assert s["by_category"]["novostroiki"] == 5
    assert s["source"] == "db"


def test_compute_stats_empty_db(tmp_path):
    path = tmp_path / "empty.db"
    db.init_db(path)

    result = stats.compute_stats(path)

    assert result["total_listings"] == 0
    assert result["median_price"] == 0
    assert result["median_ppsm"] == 0
    assert result["by_district"] == []


def test_compute_stats_missing_db(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        stats.compute_stats(tmp_path / "nope.db")


def test_heatmap_points(tmp_path):
    """Сетка карты: группировка в ячейки, фильтры активности и min_n."""
    from krisha.stats import heatmap_points

    path = tmp_path / "krisha.db"
    db.init_db(path)
    base = {
        "url": None, "title": None, "rooms": 2, "source": "test",
        "price": 50_000_000, "area": 50.0, "district": "Bostandykskiy_r-n",
    }
    rows = [
        # две точки в одной ячейке (~40 м друг от друга)
        {"id": 1, "lat": 43.2000, "lon": 76.9000},
        {"id": 2, "lat": 43.2003, "lon": 76.9003, "price": 60_000_000},
        # одиночка в другой ячейке — отсеивается min_n=2
        {"id": 3, "lat": 43.30, "lon": 76.95},
        # без координат — не участвует
        {"id": 4, "lat": None, "lon": None},
    ]
    for r in rows:
        db.upsert_listing({**base, "url": f"https://krisha.kz/a/show/{r['id']}", **r}, db_path=path)

    points = heatmap_points(db_path=path)
    assert len(points) == 1
    pt = points[0]
    assert pt["n"] == 2
    assert pt["ppsm"] == round((50_000_000 / 50 + 60_000_000 / 50) / 2)
    assert abs(pt["lat"] - 43.2) < 0.005 and abs(pt["lon"] - 76.9) < 0.005


def test_heatmap_points_missing_db(tmp_path):
    from krisha.stats import heatmap_points

    try:
        heatmap_points(db_path=tmp_path / "nope.db")
        raise AssertionError("должен быть FileNotFoundError")
    except FileNotFoundError:
        pass
