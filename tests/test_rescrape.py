"""Тесты шардированного рескрейпа (этап 4)."""

import pytest

from krisha.config import ALMATY_DISTRICT_SLUGS, ROOM_SHARDS
from krisha.db import get_conn, init_db, upsert_listing
from krisha.scraping import rescrape
from krisha.scraping.rescrape import shard_urls, sweep


@pytest.fixture(autouse=True)
def _isolate_parse_rate_history(tmp_path, monkeypatch):
    """sweep() пишет data/rescrape_history_<deal>.json на диск (issue #97) —
    переносим это на tmp_path, чтобы тесты не трогали реальный data/ репо."""
    monkeypatch.setattr(rescrape, "DATA_DIR", tmp_path)


def _card(lid: int, price: int) -> str:
    return (
        f'<div data-id="{lid}" class="a-card"><a href="/a/show/{lid}">x</a>'
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
    # Остальные 31 шард в этом тесте отдают пустую первую страницу (нет
    # override в pages) — с фиксом issue #96 это больше не «покрытие», а
    # подозрительный шард. Проверяем это отдельным тестом ниже; здесь важно,
    # что целевой шард (с реальными карточками) в failed_shards не попал.
    assert len(stats["failed_shards"]) == 31
    assert shards[0][0] not in stats["failed_shards"]
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
    assert stats["delisted"] is None
    with get_conn(db) as conn:  # объявление НЕ помечено снятым
        assert conn.execute("SELECT is_active FROM listings WHERE id=333").fetchone()[0] == 1


def test_sweep_treats_all_empty_shards_as_uncovered_and_skips_delist(tmp_path, monkeypatch):
    """issue #96: страница загрузилась (200), но 0 валидных id → шард НЕ покрыт.

    Раньше это считалось успехом и живые объявления рисковали получить
    ложный delist; теперь такие шарды идут в failed_shards и delist
    полностью пропускается, как и при сетевых сбоях.
    """
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(444), db)
    with get_conn(db) as conn:
        conn.execute("UPDATE listings SET last_seen = datetime('now', '-10 days')")

    client = FakeClient({})  # все шарды загрузились, но карточек 0 (пустая выдача/антибот)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=5, max_new_details=10, db_path=db)

    assert len(stats["failed_shards"]) == 32
    assert stats["delisted"] is None
    with get_conn(db) as conn:  # объявление НЕ помечено снятым
        assert conn.execute("SELECT is_active FROM listings WHERE id=444").fetchone()[0] == 1


def test_sweep_delists_when_shards_report_nonempty_coverage(tmp_path, monkeypatch):
    """Контрольный сценарий: шарды дают непустые первые страницы → покрытие
    засчитывается как раньше, delist для давно не виденных объявлений идёт."""
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(444), db)
    with get_conn(db) as conn:
        conn.execute("UPDATE listings SET last_seen = datetime('now', '-10 days')")

    class CoveredClient(FakeClient):
        def get(self, url: str) -> str | None:
            self.requested.append(url)
            dummy_id = (abs(hash(url)) % 900_000) + 100_000
            return _card(dummy_id, 5_000_000)

    client = CoveredClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: None)

    stats = sweep(max_pages=5, max_new_details=10, db_path=db)

    assert stats["failed_shards"] == []
    assert stats["delisted"] == 1
    with get_conn(db) as conn:
        assert conn.execute("SELECT is_active FROM listings WHERE id=444").fetchone()[0] == 0


def test_sweep_shard_stops_on_antibot_signature(tmp_path, monkeypatch):
    """issue #96: сигнатура анти-бот/капча страницы на первой странице шарда
    останавливает шард (даже если в HTML случайно нашёлся валидный data-id)."""
    db = tmp_path / "test.db"
    init_db(db)

    shards = shard_urls()
    first_url = shards[0][1]
    antibot_html = "<html><body>Подтвердите, что вы не робот" + _card(999, 1) + "</body></html>"
    client = FakeClient({first_url: antibot_html})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=5, max_new_details=10, db_path=db)

    assert shards[0][0] in stats["failed_shards"]
    assert stats["found_in_search"] == 0  # антибот-шард не отдал ни одной цены


def test_sweep_marks_run_suspicious_on_parse_rate_drop(tmp_path, monkeypatch):
    """issue #97: found_in_search сильно ниже медианы последних проходов —
    проход помечается suspicious даже когда все шарды формально покрыты."""
    db = tmp_path / "test.db"
    init_db(db)
    monkeypatch.setattr(rescrape, "DATA_DIR", tmp_path)

    class CoveredClient(FakeClient):
        def get(self, url: str) -> str | None:
            self.requested.append(url)
            dummy_id = (abs(hash(url)) % 900_000) + 100_000
            return _card(dummy_id, 1)

    # Прогоняем 3 «здоровых» прохода (все 32 шарда покрыты, по 1 карточке) —
    # накапливаем историю found_in_search=32.
    for _ in range(3):
        client = CoveredClient({})
        monkeypatch.setattr(rescrape, "PoliteClient", lambda c=client: c)
        monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: None)
        stats = sweep(max_pages=1, max_new_details=0, db_path=db, deal="prodazha")
        assert stats["suspicious"] is False

    # Четвёртый проход: сильно меньше объявлений (антибот на бОльшую часть шардов).
    class DegradedClient(FakeClient):
        def get(self, url: str) -> str | None:
            self.requested.append(url)
            return "<html></html>"  # все шарды пустые → 0 found

    client = DegradedClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    stats = sweep(max_pages=1, max_new_details=0, db_path=db, deal="prodazha")

    assert stats["found_in_search"] == 0
    assert stats["parse_rate_median_7"] == 32
    assert stats["suspicious"] is True


def test_sweep_marks_suspicious_via_active_db_baseline(tmp_path, monkeypatch):
    """issue #97 (ревью Декса на PR #125): прод-детект не может опираться на
    файл истории — GitHub Actions раннер каждый запуск чистый, файл в
    .gitignore, история никогда не наберёт 3 точки. Основная защита должна
    сработать по количеству активных объявлений в самой БД (приходит с
    раннером как артефакт), даже с абсолютно пустой историей проходов."""
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.executemany(
            "INSERT INTO listings (id, url, price, is_active) VALUES (?, ?, ?, 1)",
            [(i, f"https://krisha.kz/a/show/{i}", 10_000_000) for i in range(1, 201)],
        )

    class DegradedClient(FakeClient):
        def get(self, url: str) -> str | None:
            self.requested.append(url)
            return "<html></html>"  # все шарды пустые → 0 found

    client = DegradedClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=1, max_new_details=0, db_path=db, deal="prodazha")

    assert stats["active_in_db_before"] == 200
    assert stats["parse_rate_median_7"] is None  # история пустая — «раннер чистый»
    assert stats["suspicious"] is True  # но DB-базлайн всё равно сработал


def test_sweep_not_suspicious_when_too_few_active_in_db(tmp_path, monkeypatch):
    """Холодная/тестовая БД с << MIN_ACTIVE_IN_DB_FOR_CHECK активных объявлений
    не должна давать ложных suspicious — сравнивать не с чем."""
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.executemany(
            "INSERT INTO listings (id, url, price, is_active) VALUES (?, ?, ?, 1)",
            [(i, f"https://krisha.kz/a/show/{i}", 10_000_000) for i in range(1, 6)],
        )

    class DegradedClient(FakeClient):
        def get(self, url: str) -> str | None:
            self.requested.append(url)
            return "<html></html>"

    client = DegradedClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=1, max_new_details=0, db_path=db, deal="prodazha")

    assert stats["active_in_db_before"] == 5
    assert stats["suspicious"] is False


# ---------- issue #127: sighting для всех найденных id + backlog по приоритету ----------


def test_sweep_records_sighting_for_ids_beyond_detail_limit(tmp_path, monkeypatch):
    """Раньше id за пределами max_new_details не получали даже first_seen —
    теперь sighting пишется для КАЖДОГО найденного id, независимо от лимита
    на детальный fetch."""
    db = tmp_path / "test.db"
    init_db(db)

    shards = shard_urls()
    first_url = shards[0][1]
    # 3 новых id на первой странице первого шарда
    pages = {first_url: _card(1, 10_000_000) + _card(2, 20_000_000) + _card(3, 30_000_000)}
    client = FakeClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: _listing(int(url.rsplit("/", 1)[-1])))

    stats = sweep(max_pages=1, max_new_details=1, db_path=db)  # лимит детали — только 1

    assert stats["found_in_search"] == 3
    assert stats["new_listings"] == 1  # детали докачали только для одного
    with get_conn(db) as conn:
        # но sighting-строки есть у ВСЕХ трёх (первым делом — first_seen/цена)
        assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 3
        titles = {
            r[0]: r[1]
            for r in conn.execute("SELECT id, title FROM listings").fetchall()
        }
    detailed = [lid for lid, title in titles.items() if title is not None]
    sighting_only = [lid for lid, title in titles.items() if title is None]
    assert len(detailed) == 1
    assert len(sighting_only) == 2
    # backlog виден в stats — есть ещё недокачанные детали
    assert stats["detail_queue_after"] == 2


def test_sweep_detail_queue_prioritizes_oldest_backlog_first(tmp_path, monkeypatch):
    """Приоритет очереди — самые старые sighting-only лоты, а не то, что нашли
    первым в текущем проходе (чтобы шард, идущий первым в обходе, не всегда
    «съедал» весь лимит детального fetch)."""
    db = tmp_path / "test.db"
    init_db(db)
    from krisha.db import record_sighting

    # backlog из "прошлого" прохода: 2 старых непрокачанных лота
    record_sighting(100, "https://krisha.kz/a/show/100", 1, db)
    record_sighting(101, "https://krisha.kz/a/show/101", 1, db)
    # backdate явно: datetime('now') в schema — секундная точность, тест
    # должен быть детерминирован независимо от скорости выполнения.
    with get_conn(db) as conn:
        conn.execute(
            "UPDATE listings SET first_seen = '2020-01-01 00:00:00' WHERE id IN (100, 101)"
        )

    shards = shard_urls()
    first_url = shards[0][1]
    # в текущем проходе шард сразу находит новый id 999 первым
    pages = {first_url: _card(999, 5_000_000) + _card(100, 1) + _card(101, 1)}
    client = FakeClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: _listing(int(url.rsplit("/", 1)[-1])))

    stats = sweep(max_pages=1, max_new_details=2, db_path=db)  # хватит на 2 из 3

    with get_conn(db) as conn:
        titles = {r[0]: r[1] for r in conn.execute("SELECT id, title FROM listings").fetchall()}
    # старые 100/101 обработаны раньше нового 999 (FIFO по first_seen)
    assert titles[100] is not None
    assert titles[101] is not None
    assert titles[999] is None  # ещё в очереди
    assert stats["detail_queue_after"] == 1


def test_sweep_detail_queue_skips_delisted_sighting(tmp_path, monkeypatch):
    """Лот получил sighting, но был снят с продажи до того, как очередь
    деталей до него дошла (is_active=0, title навсегда NULL) — такой
    "труп" не должен застревать в голове FIFO и съедать бюджет докачки на
    каждом проходе."""
    db = tmp_path / "test.db"
    init_db(db)
    from krisha.db import record_sighting

    record_sighting(100, "https://krisha.kz/a/show/100", 1, db)
    with get_conn(db) as conn:
        conn.execute(
            "UPDATE listings SET first_seen = '2020-01-01 00:00:00', is_active = 0 "
            "WHERE id = 100"
        )

    shards = shard_urls()
    first_url = shards[0][1]
    pages = {first_url: _card(999, 5_000_000)}
    client = FakeClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: _listing(int(url.rsplit("/", 1)[-1])))

    stats = sweep(max_pages=1, max_new_details=5, db_path=db)

    with get_conn(db) as conn:
        titles = {r[0]: r[1] for r in conn.execute("SELECT id, title FROM listings").fetchall()}
    # делистнутый 100 не попал в to_fetch (title всё ещё NULL), а бюджет
    # ушёл на реально живой новый лот 999
    assert titles[100] is None
    assert titles[999] is not None
    # очередь до фетча — только живой 999 (свежий sighting), мёртвый 100 не
    # учитывается; после фетча очередь пуста
    assert stats["detail_queue_before"] == 1
    assert stats["detail_queue_after"] == 0
