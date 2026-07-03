"""Тесты шардированного рескрейпа (этап 4)."""

from krisha.config import ALMATY_DISTRICT_SLUGS, ROOM_SHARDS
from krisha.db import get_conn, init_db, upsert_listing
from krisha.scraping import rescrape
from krisha.scraping.rescrape import shard_urls, sweep


def _card(lid: int, price: int) -> str:
    return (
        f'<div data-id="{lid}"><a href="/a/show/{lid}">x</a>'
        f'<span class="a-card__price">{price:,} ₸</span></div>'.replace(",", " ")
    )


def _listing(lid: int, price: int = 10_000_000) -> dict:
    return {
        "id": lid,
        "url": f"https://krisha.kz/a/show/{lid}",
        "price": price,
        "title": "test",
        "rooms": 2,
        "area": 60.0,
    }


class FakeClient:
    """Отдаёт заранее заданный HTML по URL; незнакомый URL → пустая страница."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str) -> str | None:
        self.requested.append(url)
        return self.pages.get(url, "<html></html>")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def test_shard_urls_cover_districts_x_rooms():
    shards = shard_urls()
    assert len(shards) == len(ALMATY_DISTRICT_SLUGS) * len(ROOM_SHARDS) == 32
    urls = [url for _, url in shards]
    assert len(set(urls)) == 32  # все шарды уникальны
    for url in urls:
        assert "das[live.rooms][]=" in url
    # 4к+ покрывает и 4, и 5+ комнат
    four_plus = [u for u in urls if "das[live.rooms][]=4&das[live.rooms][]=5" in u]
    assert len(four_plus) == len(ALMATY_DISTRICT_SLUGS)
    # правильный слаг Наурызбайского района (-iy, не -ij)
    assert any("almaty-nauryzbajskiy" in u for u in urls)
    assert not any("almaty-nauryzbajskij/" in u for u in urls)


def test_shard_urls_arenda():
    shards = shard_urls("arenda")
    assert len(shards) == 32
    for _, url in shards:
        assert "/arenda/kvartiry/" in url
        assert "/prodazha/" not in url
    # продажа остаётся дефолтом
    assert all("/prodazha/kvartiry/" in url for _, url in shard_urls())


def test_sweep_arenda_uses_rent_shards(tmp_path, monkeypatch):
    """sweep(deal="arenda") ходит по арендной выдаче и пишет в свою базу."""
    db = tmp_path / "rent.db"
    shards = shard_urls("arenda")
    first_url = shards[0][1]
    pages = {first_url: _card(555, 300_000)}
    client = FakeClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: _listing(555, 300_000))

    stats = sweep(max_pages=1, max_new_details=10, db_path=db, deal="arenda")

    assert stats["new_listings"] == 1
    assert all("/arenda/" in u for u in client.requested if "krisha.kz" in u and "/a/show/" not in u)
    with get_conn(db) as conn:
        row = conn.execute("SELECT price FROM listings WHERE id = 555").fetchone()
        assert row[0] == 300_000


def test_sweep_walks_shards_and_updates_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(111, price=10_000_000), db)  # знакомое, цена изменится

    shards = shard_urls()
    first_url = shards[0][1]
    pages = {
        # шард 1: две страницы (page=2 в html → есть следующая)
        first_url: _card(111, 12_000_000) + '<a href="?page=2">2</a>',
        f"{first_url}&page=2": _card(222, 20_000_000),
    }
    client = FakeClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(
        rescrape, "parse_detail", lambda html, url: _listing(222, 20_000_000)
    )

    stats = sweep(max_pages=5, max_new_details=10, db_path=db)

    assert stats["found_in_search"] == 2
    assert stats["known_seen"] == 1
    assert stats["new_listings"] == 1
    assert stats["price_changes"] == 1
    assert stats["failed_shards"] == []
    # обошли все 32 шарда: 2 страницы первого + по одной на остальные + деталка
    assert len(client.requested) == 2 + 31 + 1
    with get_conn(db) as conn:
        assert conn.execute("SELECT price FROM listings WHERE id=111").fetchone()[0] == 12_000_000
        assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 2


def test_sweep_skips_delist_if_shard_failed(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(333), db)
    with get_conn(db) as conn:  # давно не видели — кандидат на delisted
        conn.execute("UPDATE listings SET last_seen = datetime('now', '-10 days')")

    class FailingClient(FakeClient):
        def get(self, url: str) -> str | None:
            self.requested.append(url)
            return None  # все страницы «не загрузились»

    client = FailingClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=5, max_new_details=10, db_path=db)

    assert len(stats["failed_shards"]) == 32
    assert stats["delisted"] == 0
    with get_conn(db) as conn:  # объявление НЕ помечено снятым
        assert conn.execute("SELECT is_active FROM listings WHERE id=333").fetchone()[0] == 1


def test_sweep_delists_after_full_coverage(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(444), db)
    with get_conn(db) as conn:
        conn.execute("UPDATE listings SET last_seen = datetime('now', '-10 days')")

    client = FakeClient({})  # все шарды пустые, но загрузились
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=5, max_new_details=10, db_path=db)

    assert stats["failed_shards"] == []
    assert stats["delisted"] == 1
    with get_conn(db) as conn:
        assert conn.execute("SELECT is_active FROM listings WHERE id=444").fetchone()[0] == 0
