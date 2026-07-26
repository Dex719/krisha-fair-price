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


def _listing(lid: int, price: int = 10_000_000, area: float = 60.0) -> dict:
    return {
        "id": lid,
        "url": f"https://krisha.kz/a/show/{lid}",
        "price": price,
        "title": "test",
        "rooms": 2,
        "area": area,
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
        # шард 1: две страницы (структурный пагинатор → есть следующая, issue #99)
        first_url: _card(111, 12_000_000)
        + '<a class="paginator__btn--next" href="?page=2">2</a>',
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


def test_sweep_detail_queue_drains_backlog_not_just_current_pass(tmp_path, monkeypatch):
    """Очередь докачки берёт накопленный backlog, а не только то, что нашли
    в текущем проходе — иначе шард, идущий первым в обходе, всегда съедал бы
    весь лимит детального fetch.

    issue #152: раньше порядок внутри backlog был FIFO по first_seen. Но
    first_seen внутри прохода = порядок обхода шардов = алфавит районов, так
    что «самые старые» систематически означало «Алатауский». Итог на проде:
    7094 лота с деталями в Алатауском (медиана 631 тыс ₸/м²) против 639 в
    Медеуском (996 тыс) — модель оказалась слепа там, где цены выше всего.
    Теперь порядок круговой по «район × комнаты», и этот тест проверяет уже
    только то, что backlog вообще разгребается; сам порядок — в отдельном
    тесте про round-robin."""
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
    # Бюджета хватает на 2 из 3 — накопленный backlog должен быть разгребён,
    # а не проигнорирован в пользу свежей находки текущего прохода.
    drained = sum(1 for lid in (100, 101) if titles.get(lid) is not None)
    assert drained >= 1, "backlog прошлых проходов обязан попадать в очередь"
    assert stats["detail_queue_after"] < stats["detail_queue_before"]
    # Какой именно лот остался недокачанным — теперь не свойство FIFO, а
    # результат кругового обхода, поэтому проверяем только сам факт остатка.
    assert stats["detail_queue_after"] == 1
    assert sum(1 for t in titles.values() if t is not None) == 2


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


# ---------- issue #102: повторная докачка устаревших активных деталей ----------


def test_sweep_refreshes_stale_active_listing_details(tmp_path, monkeypatch):
    """Рескрейп по карточке обновляет только цену — площадь/этаж/описание/
    координаты, отредактированные продавцом, без этой очереди никогда не
    подтягивались, пока лот жил (issue #102)."""
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(500, price=10_000_000), db)
    with get_conn(db) as conn:
        # "докачано" 60 дней назад — старше дефолтного refresh_stale_days=30
        conn.execute(
            "UPDATE listings SET scraped_at = '2020-01-01 00:00:00', area = 55.0 "
            "WHERE id = 500"
        )

    shards = shard_urls()
    first_url = shards[0][1]
    # в выдаче лот не встретился в этот проход (шард пуст) — обновление
    # устаревших деталей не должно зависеть от found в текущем проходе
    pages = {first_url: _card(999, 5_000_000)}
    client = FakeClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(
        rescrape,
        "parse_detail",
        lambda html, url: _listing(500, price=10_000_000, area=70.0) if "500" in url else None,
    )

    stats = sweep(max_pages=1, max_new_details=0, max_refresh=10, db_path=db)

    with get_conn(db) as conn:
        area = conn.execute("SELECT area FROM listings WHERE id = 500").fetchone()[0]
    assert area == 70.0  # отредактированная площадь подтянулась
    assert stats["stale_refresh_queue"] == 1
    assert stats["stale_refreshed"] == 1


def test_sweep_does_not_refresh_recently_scraped_details(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(501, price=10_000_000, area=55.0), db)  # scraped_at = now

    shards = shard_urls()
    first_url = shards[0][1]
    client = FakeClient({first_url: _card(999, 5_000_000)})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: None)

    stats = sweep(max_pages=1, max_new_details=0, max_refresh=10, db_path=db)

    assert stats["stale_refresh_queue"] == 0
    assert stats["stale_refreshed"] == 0
    with get_conn(db) as conn:
        assert conn.execute("SELECT area FROM listings WHERE id = 501").fetchone()[0] == 55.0


def test_sweep_max_refresh_zero_disables_stale_refresh(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(502, price=10_000_000), db)
    with get_conn(db) as conn:
        conn.execute("UPDATE listings SET scraped_at = '2020-01-01 00:00:00' WHERE id = 502")

    shards = shard_urls()
    first_url = shards[0][1]
    client = FakeClient({first_url: _card(999, 5_000_000)})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: None)

    stats = sweep(max_pages=1, max_new_details=0, max_refresh=0, db_path=db)

    assert stats["stale_refresh_queue"] == 0
    assert stats["stale_refreshed"] == 0
    assert not any(url.endswith("/show/502") for url in client.requested)  # деталка не запрошена


# ---------- issue #103: карточка выдачи тоже под data-contract на цену ----------


def test_sweep_quarantines_out_of_range_card_price_and_keeps_old(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(600, price=10_000_000), db)

    shards = shard_urls()
    first_url = shards[0][1]
    # карточка отдаёт битую цену (ниже PRICE_MIN) для знакомого id=600
    pages = {first_url: _card(600, 1)}
    client = FakeClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: None)

    stats = sweep(max_pages=1, max_new_details=0, max_refresh=0, db_path=db)

    assert stats["price_changes"] == 0
    with get_conn(db) as conn:
        price = conn.execute("SELECT price FROM listings WHERE id = 600").fetchone()[0]
        anomalies = conn.execute(
            "SELECT COUNT(*) FROM parse_anomalies WHERE listing_id = 600 AND field = 'price'"
        ).fetchone()[0]
    assert price == 10_000_000  # старая цена не затёрта мусором из карточки
    assert anomalies == 1


def test_sweep_aborts_early_on_ban_and_skips_delist(tmp_path, monkeypatch):
    """issue #101: BanDetected из клиента должен прервать проход досрочно —
    остальные шарды не обходятся, delisted пропускается (как при failed_shards),
    stats["banned"] выставлен, и алерт отправлен ровно один раз."""
    from krisha.scraping.client import BanDetected

    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(333), db)

    class BannedClient(FakeClient):
        def get(self, url: str) -> str | None:
            self.requested.append(url)
            raise BanDetected("3 URL подряд получили только HTTP 403")

    client = BannedClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    alerts = []
    monkeypatch.setattr(rescrape, "_alert_ban", lambda exc: alerts.append(str(exc)))

    stats = sweep(max_pages=5, max_new_details=10, db_path=db)

    assert stats["banned"] is True
    assert stats["delisted"] is None  # как при неполном покрытии — не помечаем снятые
    assert len(alerts) == 1
    # прервались на первом же шарде — не обошли оставшиеся 31
    assert len(client.requested) == 1


def test_antibot_signature_ignores_recaptcha_policy_footer():
    """Регрессия 14.07.2026: krisha.kz печатает в подвале каждой нормальной
    страницы «Этот сайт защищён сервисом reCAPTCHA», и голая подстрока
    "captcha" в _ANTIBOT_SIGNS считала капчей ЛЮБУЮ живую выдачу. Все 32
    шарда падали, found_in_search=0, --fail-empty ронял ежедневный рескрейп
    (продажа и аренда) 13 дней подряд, база протухала."""
    live_page = (
        "<html><body>"
        + _card(101, 25_000_000)
        + '<div class="footer__trademark">&copy; 2006 — 2026 «Крыша»'
        '<p class="g-recaptcha-policy">Этот сайт защищён сервисом reCAPTCHA, '
        "и к нему применяется политика конфиденциальности Google.</p></div>"
        "</body></html>"
    )
    assert rescrape._looks_like_antibot(live_page) is False

    # Настоящий челлендж/бан по-прежнему распознаётся.
    assert rescrape._looks_like_antibot(
        '<html><body><div class="g-recaptcha" data-sitekey="x"></div></body></html>'
    )
    assert rescrape._looks_like_antibot("<html><body>Attention Required! | Cloudflare</body></html>")
    assert rescrape._looks_like_antibot("<html><body>Подтвердите, что вы не робот</body></html>")


def test_sweep_covers_shard_with_recaptcha_policy_footer(tmp_path, monkeypatch):
    """Тот же баг на уровне прохода: живая выдача с подвальной оговоркой про
    reCAPTCHA должна быть покрыта, а не уйти в failed_shards с нулём цен."""
    db = tmp_path / "test.db"
    init_db(db)

    page = (
        "<html><body>"
        + _card(2001, 30_000_000)
        + '<p class="g-recaptcha-policy">Этот сайт защищён сервисом reCAPTCHA</p>'
        "</body></html>"
    )
    client = FakeClient(dict.fromkeys([u for _, u in shard_urls()], page))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=1, max_new_details=0, db_path=db)

    assert stats["failed_shards"] == []
    assert stats["found_in_search"] == 1


class _SlowClient(FakeClient):
    """Клиент с управляемыми «часами»: каждый запрос двигает время вперёд."""

    def __init__(self, pages, clock, step=60.0):
        super().__init__(pages)
        self._clock = clock
        self._step = step

    def get(self, url):
        self._clock[0] += self._step
        return super().get(url)


def test_time_budget_stops_softly_and_marks_shards_uncovered(tmp_path, monkeypatch):
    """issue #152: раннер убивает джобу по timeout-minutes ЖЁСТКО, вместе с
    шагом заливки базы — теряется вся ночная работа. Свой дедлайн обязан
    остановиться мягко, и недообойдённые шарды должны попасть в failed_shards,
    иначе их лоты уедут в delisted как «пропавшие»."""
    db = tmp_path / "test.db"
    init_db(db)

    clock = [0.0]
    monkeypatch.setattr(rescrape, "_now", lambda: clock[0])

    page = "<html><body>" + _card(4001, 30_000_000) + "</body></html>"
    client = _SlowClient(dict.fromkeys([u for _, u in shard_urls()], page), clock, step=120.0)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    # Бюджет 5 минут при 2 минутах на запрос — хватит на пару шардов.
    stats = sweep(max_pages=1, max_new_details=0, db_path=db, time_budget_min=5)

    assert stats["time_budget_hit"] is True
    assert len(stats["failed_shards"]) > 0, "необойдённые шарды обязаны быть помечены"
    assert stats["delisted"] is None, "при неполном покрытии delisted не проставляем"


def test_mass_delist_is_blocked(tmp_path, monkeypatch):
    """issue #152: если выдача частично отдала пустые 200 без анти-бот
    маркеров, кандидатов на снятие становятся десятки процентов базы.
    Пометить их снятыми — испортить ликвидность и «дни на рынке» разом,
    причём необратимо."""
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        for i in range(200):
            conn.execute(
                "INSERT INTO listings (id, url, price, area, is_active, first_seen, last_seen) "
                "VALUES (?, 'u', 30000000, 60.0, 1, datetime('now','-40 days'), "
                "datetime('now','-40 days'))",
                (9000 + i,),
            )

    # Выдача отдаёт ровно один лот — остальные 200 выглядят «пропавшими».
    page = "<html><body>" + _card(9000, 30_000_000) + "</body></html>"
    client = FakeClient(dict.fromkeys([u for _, u in shard_urls()], page))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=1, max_new_details=0, db_path=db)

    assert stats["delist_blocked"] is True
    assert stats["delisted"] is None
    with get_conn(db) as conn:
        still_active = conn.execute("SELECT COUNT(*) FROM listings WHERE is_active=1").fetchone()[0]
    assert still_active == 200, "массовое снятие должно быть заблокировано"


def test_recovery_pass_is_detected_from_observation_gap(tmp_path, monkeypatch):
    """issue #156: проход после перерыва обязан пометить себя САМ.

    Захардкоженная дата протухнет и не переживёт следующий сбой, а он
    повторяется: окно слепоты 14–26.07.2026 было не первым (02.07 разом
    ушло 1919 лотов с лагом ≈21 день). Разрыв выводится из самой базы —
    MAX(last_seen) — поэтому верен и после отката базы из старого релиза,
    и без единого нового поля в схеме.
    """
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, area, title, is_active, "
            "first_seen, last_seen) VALUES (7000, 'u', 30000000, 60.0, 't', 1, "
            "datetime('now','-40 days'), datetime('now','-13 days'))"
        )
    page = "<html><body>" + _card(7000, 30_000_000) + "</body></html>"
    client = FakeClient(dict.fromkeys([u for _, u in shard_urls()], page))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=1, max_new_details=0, db_path=db)

    assert stats["recovery_pass"] is True
    assert stats["observation_gap_days"] == pytest.approx(13.0, abs=0.1)


@pytest.mark.parametrize("gap_days", [1, 2])
def test_short_gap_is_not_flagged_as_recovery(tmp_path, monkeypatch, gap_days):
    """Обратная сторона порога: штатный суточный разрыв и ОДИН упавший крон
    (разрыв 2 дня) не должны помечать проход восстановительным.

    Это всё ещё обычный приток за один-два дня. Пометить его когортой —
    молча выкинуть двое суток нормальных данных из обучения, ликвидности и
    статистики притока. И если помечать каждый проход, маркер перестаёт
    что-либо значить, а вместе с ним ломается весь отсев.
    """
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, area, title, is_active, "
            "first_seen, last_seen) VALUES (7000, 'u', 30000000, 60.0, 't', 1, "
            f"datetime('now','-40 days'), datetime('now','-{gap_days} days'))"
        )
    page = "<html><body>" + _card(7000, 30_000_000) + "</body></html>"
    client = FakeClient(dict.fromkeys([u for _, u in shard_urls()], page))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=1, max_new_details=0, db_path=db)

    assert stats["recovery_pass"] is False
    assert stats["cohort_marked"] == 0
    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM data_gaps").fetchone()[0] == 0


def test_user_visit_during_blackout_cannot_mask_the_gap(tmp_path, monkeypatch):
    """Пользовательский предикт тоже ставит last_seen = now.

    Один человек, открывший карточку посреди слепоты, поднял бы
    MAX(last_seen) к сегодняшнему дню и замаскировал разрыв целиком —
    детектор молча перестал бы работать ровно в том сценарии, ради которого
    заведён. Поэтому разрыв считается только по скрейп-наблюдениям.
    """
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, area, title, source, is_active, "
            "first_seen, last_seen) VALUES (7000, 'u', 30000000, 60.0, 't', 'scrape', 1, "
            "datetime('now','-40 days'), datetime('now','-13 days'))"
        )
        # Лот, который посреди слепоты открыл пользователь через /api/predict.
        conn.execute(
            "INSERT INTO listings (id, url, price, area, title, source, is_active, "
            "first_seen, last_seen) VALUES (7001, 'u', 30000000, 60.0, 't', 'user', 1, "
            "datetime('now','-40 days'), datetime('now'))"
        )
    page = "<html><body>" + _card(7000, 30_000_000) + "</body></html>"
    client = FakeClient(dict.fromkeys([u for _, u in shard_urls()], page))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=1, max_new_details=0, db_path=db)

    assert stats["recovery_pass"] is True
    assert stats["observation_gap_days"] == pytest.approx(13.0, abs=0.1)


def test_recovery_pass_records_gap_and_marks_its_backfill_cohort(tmp_path, monkeypatch):
    """Полный цикл #156: проход записывает провал в data_gaps и метит
    когортой лоты, которые сам же впервые увидел.

    Смысл метки: у этих лотов first_seen — дата первого наблюдения ПОСЛЕ
    провала, а не дата публикации. Лот мог висеть на рынке месяцами (мы
    покрывали 19k из ~44k рынка), поэтому «дни на рынке» и «приток» по ним
    считать нельзя. Знакомый лот, который был в базе до провала, когортой
    НЕ метится — его first_seen честный.
    """
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, area, title, is_active, "
            "first_seen, last_seen) VALUES (7000, 'u', 30000000, 60.0, 't', 1, "
            "datetime('now','-40 days'), datetime('now','-13 days'))"
        )
    # Выдача: знакомый лот + два, которых мы не видели за время провала.
    cards = [_card(7000, 30_000_000), _card(7100, 31_000_000), _card(7101, 32_000_000)]
    page = "<html><body>" + "".join(cards) + "</body></html>"
    client = FakeClient(dict.fromkeys([u for _, u in shard_urls()], page))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    stats = sweep(max_pages=1, max_new_details=0, db_path=db)

    assert stats["recovery_pass"] is True
    assert stats["cohort_marked"] == 2
    with get_conn(db) as conn:
        gaps = conn.execute("SELECT gap_start, gap_end FROM data_gaps").fetchall()
        assert len(gaps) == 1, "провал записан ровно один раз"
        cohorts = dict(
            conn.execute("SELECT id, first_seen_cohort FROM listings").fetchall()
        )
    # gap_start = последнее наблюдение до провала, то есть 13 дней назад.
    with get_conn(db) as conn:
        age = conn.execute(
            "SELECT julianday('now') - julianday(?)", (gaps[0][0],)
        ).fetchone()[0]
    assert age == pytest.approx(13.0, abs=0.1)

    assert cohorts[7100] == cohorts[7101] == f"gap:{str(gaps[0][0])[:10]}"
    # 7000 жил до провала, поэтому НЕ в gap-когорте. В этой крошечной базе он
    # оказывается 'initial' (он же и есть самый первый сбор) — на проде под
    # 'initial' попадает реальная когорта 11–13.06, у которой first_seen это
    # дата запуска скрейпера, а не публикации.
    assert not str(cohorts[7000]).startswith("gap:"), (
        "лот, живший до провала, не должен попасть в когорту бэкфилла"
    )


def test_grace_marking_applies_only_after_incomplete_recovery(tmp_path, monkeypatch):
    """Grace-окно растягивает пометку только после НЕПОЛНОГО обхода.

    Сайтинг пишется для каждого найденного id без лимита (лимит стоит только
    на докачке деталей), поэтому при полном обходе вся волна бэкфилла
    получает first_seen за один проход. Метить следующие дни в этом случае —
    молча выбросить из ликвидности и обучения честную органику, ~850 лотов в
    сутки. Растягивать нужно ровно тогда, когда часть выдачи не добрана и
    остаток волны придёт позже.
    """
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, area, title, is_active, "
            "first_seen, last_seen) VALUES (7000, 'u', 30000000, 60.0, 't', 1, "
            "datetime('now','-40 days'), datetime('now','-13 days'))"
        )
    # Восстановительный проход, в котором один шард не отдал выдачу.
    pages = dict.fromkeys(
        [u for _, u in shard_urls()],
        "<html><body>" + _card(7000, 30_000_000) + _card(7100, 31_000_000) + "</body></html>",
    )
    broken = shard_urls()[0][1]
    pages[broken] = "<html><body></body></html>"  # 0 валидных id → шард не покрыт
    client = FakeClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    first = sweep(max_pages=1, max_new_details=0, db_path=db)
    assert first["recovery_pass"] is True
    assert first["failed_shards"], "шард обязан быть помечен непокрытым"
    with get_conn(db) as conn:
        assert conn.execute("SELECT note FROM data_gaps").fetchone()[0] == "incomplete"

    # Следующий проход разрыва уже не видит, но остаток волны ещё приходит —
    # эти лоты обязаны попасть в ту же когорту.
    pages[broken] = "<html><body>" + _card(7200, 33_000_000) + "</body></html>"
    second = sweep(max_pages=1, max_new_details=0, db_path=db)
    assert second["recovery_pass"] is False
    assert second["cohort_marked"] == 1
    with get_conn(db) as conn:
        cohort = conn.execute(
            "SELECT first_seen_cohort FROM listings WHERE id = 7200"
        ).fetchone()[0]
    assert cohort.startswith("gap:")


def test_gap_is_not_recorded_twice_on_repeated_pass(tmp_path, monkeypatch):
    """После ПОЛНОГО восстановительного прохода следующий проход когортой уже
    не метит: вся волна получила first_seen сразу, дальше идёт органика."""
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, area, title, is_active, "
            "first_seen, last_seen) VALUES (7000, 'u', 30000000, 60.0, 't', 1, "
            "datetime('now','-40 days'), datetime('now','-13 days'))"
        )
    page = "<html><body>" + _card(7000, 30_000_000) + _card(7100, 31_000_000) + "</body></html>"
    client = FakeClient(dict.fromkeys([u for _, u in shard_urls()], page))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    first = sweep(max_pages=1, max_new_details=0, db_path=db)
    assert first["cohort_marked"] == 1

    # Второй проход: разрыва уже нет (last_seen обновлён), но запись о провале
    # ещё в grace-окне — новых лотов нет, метить нечего.
    second = sweep(max_pages=1, max_new_details=0, db_path=db)
    assert second["recovery_pass"] is False
    assert second["cohort_marked"] == 0
    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM data_gaps").fetchone()[0] == 1


def test_mass_delist_block_after_gap_clears_itself_on_next_pass(tmp_path, monkeypatch):
    """Знаменатель гарда — активные ДО прохода, и блокировка не залипает.

    Кандидатом на снятие может стать только лот, который входил в проход
    активным; всё, что проход увидел впервые, только что получило
    last_seen = now и в числитель попасть не может. Поэтому делить надо на
    популяцию, из которой числитель и берётся, — иначе большой приток
    разбавляет знаменатель и глушит гард ровно тогда, когда он нужен.

    Цена этого решения — что первый проход после долгой слепоты может
    упереться в порог и не проставить снятия. Тест фиксирует, что это НЕ
    тупик: на следующем проходе активных до прохода уже больше на величину
    бэкфилла, доля падает под порог и снятие проходит само. Чинить руками
    и поднимать порог не нужно.
    """
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        # 100 старых лотов; 40 из них выдача больше не отдаёт (реально ушли).
        for i in range(100):
            conn.execute(
                "INSERT INTO listings (id, url, price, area, title, is_active, "
                "first_seen, last_seen) VALUES (?, 'u', 30000000, 60.0, 't', 1, "
                "datetime('now','-40 days'), datetime('now','-40 days'))",
                (9000 + i,),
            )

    # Проход 1: 60 знакомых выживших + 200 впервые увиденных (волна бэкфилла).
    cards = [_card(9000 + i, 30_000_000) for i in range(60)]
    cards += [_card(20_000 + i, 30_000_000) for i in range(200)]
    page = "<html><body>" + "".join(cards) + "</body></html>"
    client = FakeClient(dict.fromkeys([u for _, u in shard_urls()], page))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)

    first = sweep(max_pages=1, max_new_details=0, db_path=db)

    # 40 кандидатов от 100 активных до прохода = 40% > порога 30%.
    assert first["active_in_db_before"] == 100
    assert first["delist_blocked"] is True
    assert first["delisted"] is None
    assert first["delist_share"] == pytest.approx(0.40, abs=0.005)

    # Проход 2 по той же выдаче: активных до прохода теперь 300 (бэкфилл
    # никуда не делся, снятия не проставились), те же 40 кандидатов = 13%.
    second = sweep(max_pages=1, max_new_details=0, db_path=db)

    assert second["active_in_db_before"] == 300
    assert second["delist_blocked"] is False
    assert second["delisted"] == 40, "блокировка обязана сниматься сама, без человека"


def test_detail_queue_is_round_robin_across_districts(tmp_path):
    """issue #152: очередь шла FIFO по first_seen, а first_seen внутри прохода
    = порядок обхода шардов = алфавит районов. Алатауский выедал весь бюджет:
    7094 лота с деталями против 639 у Медеуского, причём Медеуский — один из
    самых дорогих районов, где модель и так слепа."""
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        # Алатауский нашли первым и много, Медеуский — последним и мало.
        for i in range(50):
            conn.execute(
                "INSERT INTO listings (id, url, district, rooms, is_active, first_seen) "
                "VALUES (?, 'u', 'Alatauskiy_r-n', 2, 1, datetime('now'))", (1000 + i,)
            )
        for i in range(5):
            conn.execute(
                "INSERT INTO listings (id, url, district, rooms, is_active, first_seen) "
                "VALUES (?, 'u', 'Medeuskiy_r-n', 2, 1, datetime('now'))", (2000 + i,)
            )
        rows = [
            r[0] for r in conn.execute(
                """
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(district, '?'), COALESCE(rooms, -1)
                        ORDER BY id DESC
                    ) AS rn
                    FROM listings WHERE title IS NULL AND is_active = 1
                ) ORDER BY rn, id DESC
                """
            ).fetchall()
        ]
    top10 = rows[:10]
    medeu = sum(1 for r in top10 if r >= 2000)
    assert medeu >= 4, f"недопредставленный район должен попасть в голову очереди, а не в хвост: {top10}"
