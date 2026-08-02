"""Тесты issue #166: шардирование дневной порции докачки «район × комнаты».

Критерии приёмки из issue:

- планировщик выдаёт дневную порцию с TVD ≤ 0.20 относительно заданного
  распределения стока по шардам;
- обрыв прохода в разных точках не даёт систематического перекоса (ротация);
- курсоры шардов переживают перезапуск: повторный проход не начинает с нуля
  и не пропускает страницы;
- недобор по сбойному шарду не отдаёт его квоту другим в этом же проходе;
- в логе/базе прохода виден план и факт по каждому шарду;
- суммарное число запросов за проход не выросло.
"""

import logging
import math

import pytest

from krisha.db import (
    get_conn,
    init_db,
    last_known_shard_stock,
    record_listing_shards,
    shard_backlog_window,
    sweep_pass_seq,
)
from krisha.scraping import rescrape
from krisha.scraping.client import BanDetected
from krisha.scraping.rescrape import STARVED_SHARD_STREAK, shard_urls, sweep
from krisha.scraping.shard_plan import (
    largest_remainder_quotas,
    rotated,
    rotation_offset,
    rotation_step,
    shard_district,
)
from krisha.validity import MAX_TEST_TVD, total_variation_distance


@pytest.fixture(autouse=True)
def _isolate_parse_rate_history(tmp_path, monkeypatch):
    """Как в test_rescrape: sweep() пишет data/rescrape_history_<deal>.json —
    уводим на tmp_path, чтобы не трогать реальный data/ репо."""
    monkeypatch.setattr(rescrape, "DATA_DIR", tmp_path)


def _card(lid: int, price: int = 10_000_000) -> str:
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
    """Отдаёт заданный HTML по URL; незнакомый URL → пустая страница."""

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


def _pages_by_shard(stock: dict[str, list[int]]) -> dict[str, str]:
    """HTML первой страницы каждого шарда с заданными id карточек."""
    url_by_label = dict(shard_urls())
    return {
        url_by_label[label]: "".join(_card(lid) for lid in ids)
        for label, ids in stock.items()
    }


def _parse_detail_by_url(html, url):
    return _listing(int(url.rsplit("/", 1)[-1]))


# ---------- чистый планировщик ----------


def test_quotas_proportional_to_stock_exact_split():
    q = largest_remainder_quotas({"a": 50, "b": 25, "c": 15, "d": 10}, 40)
    assert q == {"a": 20, "b": 10, "c": 6, "d": 4}
    assert sum(q.values()) == 40


def test_quotas_largest_remainder_rounding_is_deterministic():
    # 3 шарда по 1/3 стока, cap=2: два шарда получают +1 по дробной части,
    # тай-брейк детерминирован — план воспроизводим от прогона к прогону.
    q1 = largest_remainder_quotas({"a": 10, "b": 10, "c": 10}, 2)
    q2 = largest_remainder_quotas({"c": 10, "a": 10, "b": 10}, 2)
    assert sum(q1.values()) == 2
    assert q1 == q2


def test_quotas_zero_stock_shard_gets_zero_not_redistributed():
    """Квота — доля стока, а не равная 1/32: шард без стока получает 0,
    и его доля НЕ раздаётся соседям сверх их пропорции."""
    q = largest_remainder_quotas({"a": 100, "b": 0}, 10)
    assert q == {"a": 10, "b": 0}


def test_quotas_zero_cap_or_empty_stock():
    assert largest_remainder_quotas({"a": 100}, 0) == {"a": 0}
    assert largest_remainder_quotas({"a": 0, "b": 0}, 10) == {"a": 0, "b": 0}
    assert largest_remainder_quotas({}, 10) == {}


def test_rotated_shifts_order_cyclically():
    items = list("abcd")
    assert rotated(items, 0) == items
    assert rotated(items, 1) == ["b", "c", "d", "a"]
    assert rotated(items, 4) == items
    assert rotated(items, 5) == ["b", "c", "d", "a"]
    assert rotated([], 3) == []


def test_shard_district_from_label():
    assert shard_district("Алатауский 4к+") == "Алатауский"
    assert shard_district("Медеуский 1к") == "Медеуский"


# ---------- проход с квотами: состав порции (TVD) ----------


def test_daily_batch_matches_stock_composition(tmp_path, monkeypatch):
    """Приёмочный критерий: при заданном распределении стока по шардам
    дневная порция имеет TVD ≤ 0.20 относительно этого распределения —
    метрикой из validity.py, той же, что в model_meta."""
    db = tmp_path / "test.db"
    stock = {
        "Алатауский 2к": list(range(1000, 1050)),      # 50% стока
        "Бостандыкский 2к": list(range(2000, 2025)),   # 25%
        "Медеуский 1к": list(range(3000, 3015)),       # 15%
        "Турксибский 1к": list(range(4000, 4010)),     # 10%
    }
    client = FakeClient(_pages_by_shard(stock))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, max_new_details=40, db_path=db)

    # Все найденные id — новые: backlog каждого шарда глубок, докачали ровно
    # квоту (40 = 20+10+6+4 по стоку 50/25/15/10).
    assert stats["details_fetched"] == 40
    assert stats["detail_plan"] == {
        "Алатауский 2к": 20, "Бостандыкский 2к": 10, "Медеуский 1к": 6, "Турксибский 1к": 4,
    }
    # TVD фактической порции против стока — и в stats, и пересчётом той же метрикой.
    assert stats["batch_tvd_shard"] <= MAX_TEST_TVD
    assert stats["batch_tvd_district"] <= MAX_TEST_TVD
    with get_conn(db) as conn:
        fetched = {
            shard: conn.execute(
                "SELECT COUNT(*) FROM listings l "
                "JOIN listing_shards s ON s.listing_id = l.id "
                "WHERE l.title IS NOT NULL AND s.shard = ?",
                (shard,),
            ).fetchone()[0]
            for shard in stock
        }
    fetched_labels = [s for s, n in fetched.items() for _ in range(n)]
    stock_labels = [s for s, ids in stock.items() for _ in ids]
    assert total_variation_distance(fetched_labels, stock_labels) <= MAX_TEST_TVD
    # Число запросов не выросло: выдача — по странице на шард (32), детали —
    # ровно потолок 40, сверху ничего.
    detail_requests = [u for u in client.requested if "/a/show/" in u]
    search_requests = [u for u in client.requested if "/a/show/" not in u]
    assert len(detail_requests) == 40
    assert len(search_requests) == 32


def test_plan_vs_fact_written_per_shard(tmp_path, monkeypatch):
    """Приёмочный критерий: в базе прохода виден план и факт по каждому шарду
    (32 строки, включая непокрытые — у них stock NULL и квота 0)."""
    db = tmp_path / "test.db"
    stock = {"Алатауский 2к": list(range(1000, 1020)), "Медеуский 1к": list(range(2000, 2010))}
    client = FakeClient(_pages_by_shard(stock))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    sweep(max_pages=1, max_new_details=6, db_path=db)

    with get_conn(db) as conn:
        rows = {
            r[0]: r
            for r in conn.execute(
                "SELECT shard, stock, quota, fetched, backlog_before, backlog_after, "
                "cursor_after, wrapped FROM sweep_shard_stats"
            ).fetchall()
        }
    assert len(rows) == 32
    # Покрытый шард: сток 20, квота 4 (20 из 30), факт 4, backlog сошёлся.
    shard, stock_n, quota, fetched, before, after, cursor, wrapped = rows["Алатауский 2к"]
    assert (stock_n, quota, fetched, before, after) == (20, 4, 4, 20, 16)
    assert cursor is not None and wrapped == 0
    shard, stock_n, quota, fetched, before, after, cursor, wrapped = rows["Медеуский 1к"]
    assert (stock_n, quota, fetched, before, after) == (10, 2, 2, 10, 8)
    # Непокрытый шард: сток неизвестен (NULL), квота 0, факт 0.
    shard, stock_n, quota, fetched, before, after, cursor, wrapped = rows["Ауэзовский 3к"]
    assert (stock_n, quota, fetched) == (None, 0, 0)


def test_partial_failed_shard_keeps_its_sightings(tmp_path, monkeypatch):
    """Шард упал на 2-й странице: найденное на 1-й — честно увидено в выдаче
    и получает sighting/атрибуцию, но замером стока (квотой) это НЕ считается
    и delist не разрешает."""
    db = tmp_path / "test.db"
    init_db(db)
    url_by_label = dict(shard_urls())
    target_url = url_by_label["Алатауский 2к"]

    class FailOnPage2Client(FakeClient):
        def get(self, url):
            if url == f"{target_url}&page=2":
                return None  # сеть/блокировка на второй странице
            return super().get(url)

    pages = {
        target_url: _card(1001) + _card(1002)
        + '<a class="paginator__btn--next" href="?page=2">2</a>',
    }
    client = FailOnPage2Client(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=3, max_new_details=5, db_path=db)

    assert "Алатауский 2к" in stats["failed_shards"]
    assert stats["delisted"] is None  # неполное покрытие — delist запрещён
    with get_conn(db) as conn:
        seen = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT s.listing_id, s.shard FROM listing_shards s"
            ).fetchall()
        }
        row = conn.execute(
            "SELECT quota, fetched FROM sweep_shard_stats WHERE shard = 'Алатауский 2к'"
        ).fetchone()
    # Оба id с первой страницы получили атрибуцию и sighting, несмотря на сбой...
    assert seen == {1001: "Алатауский 2к", 1002: "Алатауский 2к"}
    # ...но сток по упавшему шарду не замерен (строка есть: stock NULL), и без
    # истории стока его квота 0 — недобор не выдумывается.
    assert tuple(row) == (0, 0)


# ---------- недобор не отдаёт квоту соседям ----------


def test_failing_shard_keeps_quota_unused_in_same_pass(tmp_path, monkeypatch):
    """Приёмочный критерий: сбойный шард не отдаёт свою квоту другим в этом же
    проходе — иначе один сбойный район снова перекосит день."""
    db = tmp_path / "test.db"
    stock = {
        "Алатауский 2к": list(range(1000, 1060)),   # 60% стока
        "Медеуский 1к": list(range(2000, 2040)),    # 40% стока
    }
    pages = _pages_by_shard(stock)
    medeu_ids = set(stock["Медеуский 1к"])

    class FlakyClient(FakeClient):
        def get(self, url):
            # Детальные страницы Медеуского «падают» (таймаут/404) — недобор.
            if "/a/show/" in url and int(url.rsplit("/", 1)[-1]) in medeu_ids:
                self.requested.append(url)
                return None
            return super().get(url)

    client = FlakyClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, max_new_details=10, db_path=db)

    # Квоты: Алатауский 6, Медеуский 4. Медеуский недобрал всё — но Алатауский
    # получил СВОИ 6, а не 10: суммарно докачано меньше потолка.
    assert stats["detail_plan"] == {"Алатауский 2к": 6, "Медеуский 1к": 4}
    assert stats["details_fetched"] == 6
    with get_conn(db) as conn:
        medeu_detailed = conn.execute(
            "SELECT COUNT(*) FROM listings l JOIN listing_shards s ON s.listing_id = l.id "
            "WHERE l.title IS NOT NULL AND s.shard = 'Медеуский 1к'"
        ).fetchone()[0]
        # Курсор сбойного шарда НЕ сдвинулся: недобор компенсируется следующими
        # проходами через курсор, а не чужой квотой сейчас.
        medeu_cursor = conn.execute(
            "SELECT last_id FROM shard_cursors WHERE shard = 'Медеуский 1к'"
        ).fetchone()
    assert medeu_detailed == 0
    assert medeu_cursor is None


# ---------- курсоры переживают перезапуск ----------


def test_cursors_survive_restart_and_walk_without_skips(tmp_path, monkeypatch):
    """Приёмочный критерий: курсоры шардов переживают перезапуск, повторный
    проход не начинает с нуля и не пропускает страницы — за несколько проходов
    backlog шарда докачивается целиком, каждый лот ровно один раз."""
    db = tmp_path / "test.db"
    ids = list(range(1000, 1010))  # один шард, 10 лотов в backlog
    stock = {"Алатауский 2к": ids}
    fetched_per_pass: list[list[int]] = []

    for _ in range(3):
        client = FakeClient(_pages_by_shard(stock))
        monkeypatch.setattr(rescrape, "PoliteClient", lambda c=client: c)
        parse_calls: list[int] = []

        def parse_spy(html, url, _calls=parse_calls):
            lid = int(url.rsplit("/", 1)[-1])
            _calls.append(lid)
            return _listing(lid)

        monkeypatch.setattr(rescrape, "parse_detail", parse_spy)
        sweep(max_pages=1, max_new_details=4, db_path=db)
        fetched_per_pass.append(parse_calls)

    p1, p2, p3 = fetched_per_pass
    # Проход 2 продолжил с водяной отметки, а не начал с нуля: пересечений нет.
    assert p1 == sorted(ids, reverse=True)[:4]
    assert p2 == sorted(ids, reverse=True)[4:8]
    # Дно backlog'а: остаток 2 (всё остальное уже с деталями — wrap не даёт
    # докачать их повторно).
    assert p3 == sorted(ids, reverse=True)[8:]
    # Ни один лот не пропущен и не докачан дважды.
    assert sorted(p1 + p2 + p3) == ids
    with get_conn(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM listings WHERE title IS NULL AND is_active = 1"
        ).fetchone()[0] == 0
        cursor = conn.execute(
            "SELECT last_id FROM shard_cursors WHERE shard = 'Алатауский 2к'"
        ).fetchone()[0]
        assert cursor == ids[0]  # последняя водяная отметка — хвостовой id
        # ...а план/факт каждого прохода лёг в историю
        runs = conn.execute(
            "SELECT COUNT(*) FROM sweep_shard_stats WHERE shard = 'Алатауский 2к'"
        ).fetchone()[0]
        assert runs == 3


def test_cursor_wraps_to_head_when_bottom_reached(tmp_path, monkeypatch):
    """Круговой курсор: дойдя до дна backlog'а, окно заворачивается на голову
    выдачи — свежие лоты, пришедшие за время обхода, не ждут вечно."""
    db = tmp_path / "test.db"
    init_db(db)
    # Старый backlog: 2 лота, курсор уже ниже их (прошлые окна прошли мимо).
    old_ids = [100, 101]
    fresh_ids = [5001, 5002, 5003]  # пришли в выдачу уже после установки курсора
    with get_conn(db) as conn:
        for lid in old_ids + fresh_ids:
            conn.execute(
                "INSERT INTO listings (id, url, is_active, first_seen) "
                "VALUES (?, 'u', 1, datetime('now'))", (lid,)
            )
        conn.execute(
            "INSERT INTO shard_cursors (shard, last_id) VALUES ('Алатауский 2к', 2000)"
        )
    record_listing_shards([(lid, "Алатауский 2к") for lid in old_ids + fresh_ids], db)
    with get_conn(db) as conn:
        window, wrapped = shard_backlog_window(conn, "Алатауский 2к", cursor_id=2000, limit=5)
    assert sorted(window, reverse=True) == sorted(fresh_ids + old_ids, reverse=True)
    assert wrapped, "после исчерпания лотов ниже отметки окно обязано завернуться на голову"


# ---------- замороженный шард: событие, а не тишина (issue #168) ----------


def test_starved_shard_raises_flag_after_streak(tmp_path, monkeypatch, caplog):
    """Приёмочный критерий issue #168: шард с непустым backlog'ом и нулевой
    квотой STARVED_SHARD_STREAK проходов подряд поднимает флаг в итогах
    прохода (stats["starved_shards"]) и logger.error. Сценарий прода: шард
    хронически не покрыт (выдача глубже max_pages → сток NULL → без строки в
    last_known_shard_stock → квота 0 на каждом проходе), атрибуция при этом
    есть — unattributed_backlog_count молчит и TVD порции шард не видит."""
    db = tmp_path / "test.db"
    init_db(db)
    # Backlog с атрибуцией: два лота видены в шарде «Алатауский 2к» и ждут
    # докачки деталей (title IS NULL).
    with get_conn(db) as conn:
        for lid in (1001, 1002):
            conn.execute(
                "INSERT INTO listings (id, url, is_active, first_seen) "
                "VALUES (?, 'u', 1, datetime('now'))",
                (lid,),
            )
    record_listing_shards([(1001, "Алатауский 2к"), (1002, "Алатауский 2к")], db)

    # Выдача пуста: ни один шард не покрыт, сток нигде не замерен → все
    # квоты 0, но backlog есть только у «Алатауский 2к».
    client = FakeClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    # Порог серии — константа из rescrape: первые проходы серии ещё не флаг.
    for pass_no in range(1, STARVED_SHARD_STREAK):
        stats = sweep(max_pages=1, max_new_details=5, db_path=db)
        assert stats["starved_shards"] == [], (pass_no, stats["starved_shards"])

    with caplog.at_level(logging.ERROR, logger="krisha.scraping.rescrape"):
        stats = sweep(max_pages=1, max_new_details=5, db_path=db)
    assert stats["starved_shards"] == ["Алатауский 2к"]
    assert any(
        "Алатауский 2к" in r.getMessage() and "нулевой квотой" in r.getMessage()
        for r in caplog.records
    ), "ожидался logger.error про замороженный шард"

    # Шард покрылся (сток замерен → квота появилась) — флаг сбрасывается,
    # серия не липнет.
    client = FakeClient(_pages_by_shard({"Алатауский 2к": [1001, 1002]}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    stats = sweep(max_pages=1, max_new_details=5, db_path=db)
    assert stats["starved_shards"] == []
    assert stats["details_fetched"] == 2  # и backlog наконец дренируется


# ---------- обрыв прохода без систематического перекоса ----------


def test_interruption_hits_different_shards_on_consecutive_passes(tmp_path, monkeypatch):
    """Приёмочный критерий: обрыв прохода в разных точках не даёт
    систематического перекоса — порядок обхода ротируется между запусками,
    поэтому ампутированный хвост гуляет по шардам, а не обрезает один и тот
    же район каждый день."""
    db = tmp_path / "test.db"
    # Четыре шарда, которые ротация ставит первыми в первых четырёх проходах:
    # номер инкрементируется в начале прохода (issue #168), поэтому это
    # проходы 1–4. Смещение считается шагом, взаимно простым с 32 (ревью
    # #166), поэтому это НЕ соседи по алфавиту — и это суть проверки.
    all_shards = shard_urls()
    first_labels = [all_shards[rotation_offset(seq, 32)][0] for seq in range(1, 5)]
    assert len(set(first_labels)) == 4
    stock = {
        label: list(range((i + 1) * 1000, (i + 1) * 1000 + 25))
        for i, label in enumerate(first_labels)
    }
    pages = _pages_by_shard(stock)
    clock = [0.0]
    monkeypatch.setattr(rescrape, "_now", lambda: clock[0])

    class ClockClient(FakeClient):
        def get(self, url):
            # Выдача быстрая (6 сек/стр), детали дорогие (2 мин/шт) — бюджет
            # кончается на докачке после первого же шарда.
            clock[0] += 120.0 if "/a/show/" in url else 6.0
            return super().get(url)

    fetched_by_pass: list[set[int]] = []
    for _ in range(4):
        client = ClockClient(pages)
        monkeypatch.setattr(rescrape, "PoliteClient", lambda c=client: c)
        monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)
        stats = sweep(max_pages=1, max_new_details=8, db_path=db, time_budget_min=6)
        assert stats["time_budget_hit"] is True
        with get_conn(db) as conn:
            fetched_by_pass.append(
                {
                    r[0]
                    for r in conn.execute(
                        "SELECT s.shard FROM listings l "
                        "JOIN listing_shards s ON s.listing_id = l.id "
                        "WHERE l.title IS NOT NULL"
                    ).fetchall()
                }
            )

    # Каждый проход успел докачать (хотя бы один лот) только первый шард
    # своей ротации — и это КАЖДЫЙ РАЗ ДРУГОЙ шард: за 4 оборванных прохода
    # все 4 шарда получили свою квоту, систематического «дня одного района»
    # нет (при шаге +1 это были бы 4 соседа по алфавиту).
    cumulative: set[int] = set()
    daily: list[set[int]] = []
    for pass_set in fetched_by_pass:
        daily.append(pass_set - cumulative)
        cumulative |= pass_set
    assert all(len(d) == 1 for d in daily), daily
    assert cumulative == set(stock), cumulative


def test_rotation_offset_advances_between_passes(tmp_path, monkeypatch):
    """Номер прохода монотонен +1 и переживает перезапуск — даже если проходы
    идут в одну секунду (started_at вторичен). Смещение ротации считается из
    номера шагом, взаимно простым с 32 (ревью #166): +1 давал бы кластерные
    серии непокрытия.

    issue #168: номер инкрементируется в НАЧАЛЕ прохода, поэтому первый
    проход работает уже под номером 1, а чтение счётчика до старта прохода
    даёт номер ПРЕДЫДУЩЕГО завершённого (0 на холодной базе)."""
    db = tmp_path / "test.db"
    client = FakeClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)
    seqs = []
    for _ in range(3):
        with get_conn(db) as conn:
            seqs.append(sweep_pass_seq(conn, "prodazha"))
        sweep(max_pages=1, max_new_details=0, db_path=db)
    assert seqs == [0, 1, 2]  # счётчик до старта: 0 — холодная база
    with get_conn(db) as conn:
        used = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT run_seq FROM sweep_shard_stats ORDER BY run_seq"
            ).fetchall()
        ]
    assert used == [1, 2, 3]  # проход работает под УЖЕ инкрементированным номером
    offsets = [rotation_offset(seq, 32) for seq in used]
    step = rotation_step(32)
    assert offsets == [step, (2 * step) % 32, (3 * step) % 32]


def test_killed_pass_does_not_freeze_rotation(tmp_path, monkeypatch):
    """Приёмочный критерий issue #168: проход, убитый до записи итогов
    (исключение внутри sweep — аналог жёсткого kill раннера по
    timeout-minutes, который рвёт job вместе с транзакцией итогов), НЕ
    приводит к повторению смещения ротации на следующем запуске: pass_seq
    инкрементируется в начале прохода отдельной транзакцией. До правки
    счётчик жил в транзакции итогов — kill её рвал, следующий запуск
    повторял то же смещение и ампутировал тот же хвост шардов, а причина
    обрыва систематическая — значит, и перекос был бы систематическим."""
    db = tmp_path / "test.db"
    pages = _pages_by_shard({"Алатауский 2к": list(range(1000, 1010))})
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    class ExplodingClient(FakeClient):
        def get(self, url):
            raise RuntimeError("kill -9 посреди фазы выдачи")

    monkeypatch.setattr(rescrape, "PoliteClient", lambda: ExplodingClient(pages))
    with pytest.raises(RuntimeError, match="kill -9"):
        sweep(max_pages=1, max_new_details=5, db_path=db)
    with get_conn(db) as conn:
        # Убитый проход номер ПОЛУЧИЛ (номера не обязаны быть плотными),
        # хотя итогов не записал.
        assert sweep_pass_seq(conn, "prodazha") == 1
        assert conn.execute("SELECT COUNT(*) FROM sweep_shard_stats").fetchone()[0] == 0

    client = FakeClient(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    sweep(max_pages=1, max_new_details=5, db_path=db)

    with get_conn(db) as conn:
        assert sweep_pass_seq(conn, "prodazha") == 2
        run_seqs = {
            r[0] for r in conn.execute("SELECT run_seq FROM sweep_shard_stats").fetchall()
        }
    # Живой проход ушёл под НОВЫМ номером — то есть с другим смещением
    # ротации, а не с повтором убитого (до правки здесь был бы run_seq = 0).
    assert run_seqs == {2}
    # ...и порядок обхода реально другой: первый запрошенный шард выдачи —
    # не нулевой элемент списка, а смещённый шагом 13.
    first_search_url = next(u for u in client.requested if "/a/show/" not in u)
    assert first_search_url == rotated(shard_urls(), rotation_offset(2, 32))[0][1]


# ---------- ротация: шаг, взаимно простой с 32 (ревью #166) ----------


def test_rotation_step_bounds_uncovered_runs():
    """Приёмочный критерий ревью: при покрытии k из 32 максимальная длина
    серии непокрытия одного шарда ограничена. Шаг +1 давал серию 32−k подряд
    (12 дней при k=20); шаг 13 (gcd(13,32)=1) разносит непокрытие по всему
    диапазону покрытий. Перебираются ВСЕ покрытия k от 1 до 31 (issue #168:
    при жёстких обрывах покрывается меньше половины шардов — и граница
    обязана держаться и там, где проблема опаснее всего).

    Граница зависит от окна непокрытия u = 32−k и подтверждена тем же
    перебором, что в тесте (32 шарда × 320 проходов = 10 полных периодов
    ротации; walk по остаткам имеет период 32, поэтому 320 проходов дают
    точный супремум по бесконечной последовательности):

    - u ≤ 13 (= шагу): серия ≤ 1 — между двумя пропусками одного шарда
      смещение проскакивает его дугу целиком;
    - u ≤ 19 (= n − шаг): серия ≤ 2 — два подряд пропуска «стоят» 26 > 19
      единиц длины дуги, третий не помещается;
    - u ≤ 25 (= 2·шаг − 1): серия ≤ 4 — дуга шире двух шагов, обход
      заворачивается (d−26 ≡ d+6 mod 32) и даёт ещё до двух попаданий;
    - дальше серия растёт линейно (при k ≤ 6 покрытых позиций слишком мало,
      чтобы чаще разбивать серию). При k = 1 серия 31 = u: это минимум для
      ЛЮБОГО шага — каждый шард покрывается ровно раз за 32-проходный период
      (шаг — биекция по mod 32), лучше не бывает; для k ≥ 2 граница строго
      меньше u, тогда как шаг +1 дал бы ровно u подряд при любом k.
    """
    n = 32
    step = rotation_step(n)
    assert step > 1, "шаг 1 сохраняет кластеризацию — регресс"
    assert math.gcd(step, n) == 1, "шаг обязан быть взаимно простым с числом шардов"

    def bound(u: int) -> int:
        if u <= step:
            return 1
        if u <= n - step:
            return 2
        if u <= 2 * step - 1:
            return 4
        return 5 * (u - (2 * step - 1)) + 1

    order = list(range(n))
    for k in range(1, n):  # все покрытия: от «выжил один шард» до «не покрыт один»
        u = n - k
        b = bound(u)
        # шаг +1 дал бы серию ровно u при любом k; шаг 13 строго лучше везде,
        # кроме двух вырожденных концов: u = 1 (один непокрытый за проход —
        # серия 1 даётся биекцией сама) и u = 31 (один покрытый — серия 31
        # есть минимум для любого шага: каждый шард покрыт раз за период)
        assert b <= u, (k, b)
        assert b < u or u in (1, n - 1), (k, b)
        for shard in range(n):
            run = worst = 0
            for seq in range(320):
                covered = set(rotated(order, rotation_offset(seq, n))[:k])
                if shard in covered:
                    run = 0
                else:
                    run += 1
                    worst = max(worst, run)
            assert worst <= b, (k, shard, worst, b)


def test_shard_stats_keyed_by_run_seq_not_started_at(tmp_path, monkeypatch):
    """Ревью #166: PK sweep_shard_stats — (run_seq, shard): два прохода в одну
    секунду не затирают друг друга, а last_known_shard_stock берёт последний
    замер по монотонному run_seq, а не по секундному started_at."""
    db = tmp_path / "test.db"
    stock1 = {"Алатауский 2к": list(range(1000, 1005))}
    stock2 = {"Алатауский 2к": list(range(1000, 1008))}
    for stock in (stock1, stock2):
        client = FakeClient(_pages_by_shard(stock))
        monkeypatch.setattr(rescrape, "PoliteClient", lambda c=client: c)
        monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)
        sweep(max_pages=1, max_new_details=0, db_path=db)  # без backdate — та же секунда
    with get_conn(db) as conn:
        rows = conn.execute(
            "SELECT run_seq, stock FROM sweep_shard_stats WHERE shard = 'Алатауский 2к' "
            "ORDER BY run_seq"
        ).fetchall()
        fallback = last_known_shard_stock(conn)
    rows = [tuple(r) for r in rows]
    # обе строки живы, несмотря на общий started_at; нумерация с 1 — номер
    # инкрементируется в начале прохода (issue #168)
    assert rows == [(1, 5), (2, 8)]
    assert fallback["Алатауский 2к"] == 8  # последний замер по run_seq


# ---------- бэкфилл атрибуции из деталей (ревью #166) ----------


def _insert_detailed(db, lid, district, rooms):
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, title, district, rooms, is_active) "
            "VALUES (?, ?, 't', ?, ?, 1)",
            (lid, f"https://krisha.kz/a/show/{lid}", district, rooms),
        )


def test_init_db_backfills_shards_from_details_once(tmp_path):
    """Лоты с деталями получают точную атрибуцию из (district, rooms) РАЗОВО
    (issue #168): бэкфилл — миграция под сентинелом sweep_state на первом
    init_db базы, а не полный проход по listings при каждом старте (старт
    API включительно). Фильтры шардов непересекаются, поэтому шард из
    деталей — не оценка. Лоты вне 8 районов и без комнат/района пропускаются,
    sighting-only (title IS NULL) не атрибутируются — их шард знает только
    выдача. Свежую атрибуцию выдачи (INSERT OR REPLACE) бэкфилл не перетирает.
    Лоты, появившиеся ПОСЛЕ миграции, init_db больше не атрибутирует (этим и
    куплен дешёвый старт) — разовый инструмент остаётся доступен напрямую."""
    from krisha.db import backfill_listing_shards_from_details

    db = tmp_path / "test.db"
    init_db(db)  # свежая база: миграция на пустой listings, сентинел встал
    _insert_detailed(db, 1, "Bostandykskiy_r-n", 2)
    _insert_detailed(db, 2, "Medeuskiy_r-n", 7)   # 4к+ = «4 и 5+»
    _insert_detailed(db, 3, "Saratov_r-n", 2)     # вне Алматы — шарда нет
    _insert_detailed(db, 4, None, 2)              # без района
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, is_active) VALUES (5, 'u5', 1)"  # sighting-only
        )
        conn.execute(
            "INSERT INTO listing_shards (listing_id, shard) VALUES (1, 'Алатауский 2к')"
        )  # свежая атрибуция выдачи — бэкфилл не должен её перетереть
        # Симуляция прода, приехавшего релизом: там детальные лоты есть,
        # а сентинела миграции нет (код #168 база ещё не видела) — снимаем
        # его, и следующий init_db обязан доиграть миграцию ровно раз.
        conn.execute("DELETE FROM sweep_state WHERE key LIKE 'migrate:%'")
    init_db(db)  # миграция исполняется
    init_db(db)  # повторный старт — сентинел на месте, полного прохода нет
    with get_conn(db) as conn:
        m = dict(conn.execute("SELECT listing_id, shard FROM listing_shards").fetchall())
    assert m == {1: "Алатауский 2к", 2: "Медеуский 4к+"}

    # Лот, добавленный после миграции: init_db его больше НЕ атрибутирует
    # (этим и куплен дешёвый старт), а разовый инструмент — атрибутирует.
    _insert_detailed(db, 6, "Auezovskiy_r-n", 1)
    init_db(db)
    with get_conn(db) as conn:
        m = dict(conn.execute("SELECT listing_id, shard FROM listing_shards").fetchall())
    assert 6 not in m
    backfill_listing_shards_from_details(db)
    with get_conn(db) as conn:
        m = dict(conn.execute("SELECT listing_id, shard FROM listing_shards").fetchall())
    assert m[6] == "Ауэзовский 1к"


# ---------- бан и усечение: частичные страницы и гард #152 (ревью #166) ----------


def test_banned_shard_keeps_partial_sightings(tmp_path, monkeypatch):
    """Ревью #166: BanDetected на 2-й странице шарда — частично загруженные
    страницы переживают бан так же, как при сетевом сбое: id получают
    sighting/атрибуцию (до #166 они выживали в общем словаре). Проход
    останавливается, сток шарда не замерен, delist запрещён."""
    db = tmp_path / "test.db"
    init_db(db)
    url_by_label = dict(shard_urls())
    target_url = url_by_label["Алатауский 2к"]

    class BanOnPage2Client(FakeClient):
        def get(self, url):
            if url == f"{target_url}&page=2":
                raise BanDetected("серия 403")
            return super().get(url)

    pages = {
        target_url: _card(1001) + _card(1002)
        + '<a class="paginator__btn--next" href="?page=2">2</a>',
    }
    client = BanOnPage2Client(pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)
    monkeypatch.setattr(rescrape, "_alert_ban", lambda exc: None)

    stats = sweep(max_pages=3, max_new_details=5, db_path=db)

    assert stats["banned"] is True
    assert stats["banned_phase"] == "search"
    assert "Алатауский 2к" in stats["failed_shards"]
    assert stats["delisted"] is None
    with get_conn(db) as conn:
        seen = dict(
            conn.execute("SELECT listing_id, shard FROM listing_shards").fetchall()
        )
        row = conn.execute(
            "SELECT stock FROM sweep_shard_stats WHERE shard = 'Алатауский 2к'"
        ).fetchone()
    assert seen == {1001: "Алатауский 2к", 1002: "Алатауский 2к"}
    assert row[0] is None  # сток по забаненному шарду не замерен


def test_shard_deeper_than_max_pages_is_not_covered(tmp_path, monkeypatch):
    """Гард-остаток #152 (ревью #166): дошли до max_pages, а следующая
    страница есть — покрытие шарда неполное: failed_shards, сток не замерен,
    delist запрещён, но уже увиденные id получают sighting/атрибуцию."""
    db = tmp_path / "test.db"
    init_db(db)
    url_by_label = dict(shard_urls())
    target_url = url_by_label["Алатауский 1к"]
    nxt = '<a class="paginator__btn--next" href="?page=N">N</a>'
    client = FakeClient({
        target_url: _card(1001) + nxt,
        f"{target_url}&page=2": _card(1002) + nxt,  # и дальше есть страницы
    })
    monkeypatch.setattr(rescrape, "PoliteClient", lambda: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=2, max_new_details=5, db_path=db)

    assert "Алатауский 1к" in stats["failed_shards"]
    assert stats["delisted"] is None
    with get_conn(db) as conn:
        seen = dict(
            conn.execute("SELECT listing_id, shard FROM listing_shards").fetchall()
        )
        row = conn.execute(
            "SELECT stock FROM sweep_shard_stats WHERE shard = 'Алатауский 1к'"
        ).fetchone()
    assert seen == {1001: "Алатауский 1к", 1002: "Алатауский 1к"}
    assert row[0] is None
