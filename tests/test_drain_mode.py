"""Тесты issue #152: разгон сбора — режим по backlog'у, откат по банам,
подрезка потолка по плану, диагностика без докачки.

Приёмочные критерии из issue (проверяются на PR):

- режим выбирается по backlog'у, а не по флагу запуска, и виден в итогах;
- два подряд BanDetected возвращают задержки в steady state и поднимают флаг;
- остаток квоты от шарда с мелким backlog'ом перераспределяется, а от
  сбойного или незамеренного — нет;
- план прохода, не влезающий в дедлайн по историческим таймингам, урезает
  потолок заранее.
"""


import pytest

from krisha.db import (
    get_conn,
    init_db,
    record_listing_shards,
    record_sweep_run,
)
from krisha.scraping import rescrape
from krisha.scraping.client import BanDetected
from krisha.scraping.pass_plan import DRAIN_MODE, STEADY_MODE
from krisha.scraping.rescrape import STARVED_SHARD_STREAK, shard_urls, sweep


@pytest.fixture(autouse=True)
def _isolate_parse_rate_history(tmp_path, monkeypatch):
    """Как в test_rescrape: sweep() пишет data/rescrape_history_<deal>.json —
    уводим на tmp_path, чтобы не трогать реальный data/ репо."""
    monkeypatch.setattr(rescrape, "DATA_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _no_env_delay_override(monkeypatch):
    """Явный env KRISHA_DELAY_MIN/MAX — оверрайд поверх пресета режима
    (его выставляет воркфлоу); тесты проверяют паузы ПРЕСЕТА — чистим env."""
    monkeypatch.delenv("KRISHA_DELAY_MIN", raising=False)
    monkeypatch.delenv("KRISHA_DELAY_MAX", raising=False)


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
    url_by_label = dict(shard_urls())
    return {
        url_by_label[label]: "".join(_card(lid) for lid in ids)
        for label, ids in stock.items()
    }


def _parse_detail_by_url(html, url):
    return _listing(int(url.rsplit("/", 1)[-1]))


def _seed_backlog(db, n: int, shard: str | None = None, start_id: int = 900_000) -> None:
    """n лотов backlog'а (sighting без деталей), опционально с атрибуцией."""
    with get_conn(db) as conn:
        conn.executemany(
            "INSERT INTO listings (id, url, is_active, first_seen) "
            "VALUES (?, ?, 1, datetime('now'))",
            [(start_id + i, f"https://krisha.kz/a/show/{start_id + i}") for i in range(n)],
        )
    if shard is not None:
        record_listing_shards([(start_id + i, shard) for i in range(n)], db)


# ---------- режим по backlog'у, виден в итогах (приёмочный критерий) ----------


def test_mode_drain_selected_by_big_backlog_and_visible_in_stats(tmp_path, monkeypatch):
    """Приёмочный критерий: режим выбирается по backlog'у, а не по флагу
    запуска. backlog выше порога разгона → drain-пресет (паузы 1.5–3.0,
    потолки 4500/800/30), и это видно в итогах прохода."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 5_001, shard="Алатауский 2к")

    client = FakeClient(_pages_by_shard({"Алатауский 2к": [1001, 1002]}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, db_path=db)  # БЕЗ флагов запуска — только база

    assert stats["mode"] == "drain"
    assert "backlog 5001" in stats["mode_reason"]
    assert stats["backlog_at_start"] == 5_001  # замер ДО сайтингов этого прохода
    assert stats["delay_range"] == list(DRAIN_MODE.delay_range)
    # Эффективные потолки — из пресета drain (очередь глубока, бюджет в порядке)
    assert stats["max_new_details"] == DRAIN_MODE.max_new
    # refresh-очередь пуста (все лоты — свежие sightings): эффективный
    # потолок refresh ограничен реальной очередью, пресет не накручивает
    assert stats["max_refresh"] == 0
    assert stats["refresh_stale_days"] == DRAIN_MODE.refresh_stale_days
    # режим записан в историю проходов — гистерезис следующего прохода и
    # отложенный чек-ин читают его из базы
    with get_conn(db) as conn:
        row = conn.execute("SELECT mode FROM sweep_runs").fetchone()
    assert row[0] == "drain"


def test_mode_steady_selected_by_small_backlog(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 10, shard="Алатауский 2к")

    client = FakeClient(_pages_by_shard({"Алатауский 2к": [1001]}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, db_path=db)

    assert stats["mode"] == "steady"
    assert stats["delay_range"] == list(STEADY_MODE.delay_range)
    # backlog мелкий — эффективный потолок ограничен реальной очередью
    assert stats["max_new_details"] == 11  # 10 старых + 1 свежий сайтинг
    assert stats["refresh_stale_days"] == STEADY_MODE.refresh_stale_days


def test_mode_hysteresis_keeps_previous_mode_between_thresholds(tmp_path, monkeypatch):
    """Между порогами (3000..5000) режим — по прошлому проходу: приток на
    проде превышает steady-дренаж, при едином пороге режим дребезжал бы."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 4_000, shard="Алатауский 2к")
    client = FakeClient(_pages_by_shard({"Алатауский 2к": [1001]}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    # Истории нет — steady по умолчанию (без докачки, чтобы backlog не
    # ушёл ниже порога выхода и не сломал постановку второго прохода)
    assert sweep(max_pages=1, max_new_details=0, db_path=db)["mode"] == "steady"
    # Прошлый проход был drain → продолжаем drain (гистерезис)
    with get_conn(db) as conn:
        conn.execute("UPDATE sweep_runs SET mode = 'drain'")
    stats = sweep(max_pages=1, db_path=db)
    assert stats["mode"] == "drain"
    assert "гистерезис" in stats["mode_reason"]


def test_mode_override_ignores_backlog(tmp_path, monkeypatch):
    """Явный mode=drain — ручной запуск: backlog не читается (но причина
    честно пишется в итоги)."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 5, shard="Алатауский 2к")
    client = FakeClient(_pages_by_shard({"Алатауский 2к": [1001]}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, db_path=db, mode="drain")

    assert stats["mode"] == "drain"
    assert "явно" in stats["mode_reason"]
    assert stats["delay_range"] == list(DRAIN_MODE.delay_range)


def test_env_delay_overrides_preset(tmp_path, monkeypatch):
    """Паузы из env (их ставит воркфлоу) — оверрайд поверх пресета режима."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 5, shard="Алатауский 2к")
    monkeypatch.setenv("KRISHA_DELAY_MIN", "7.5")
    monkeypatch.setenv("KRISHA_DELAY_MAX", "9.5")
    # config читает env на импорте — перечитываем его константу
    from krisha import config

    monkeypatch.setattr(config, "REQUEST_DELAY_RANGE", (7.5, 9.5))
    monkeypatch.setattr(rescrape, "REQUEST_DELAY_RANGE", (7.5, 9.5))
    client = FakeClient(_pages_by_shard({"Алатауский 2к": [1001]}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, db_path=db)

    assert stats["mode"] == "steady"  # режим — всё равно по backlog'у
    assert stats["delay_range"] == [7.5, 9.5]


def test_drain_completed_flag_on_transition(tmp_path, monkeypatch):
    """Переход drain → steady — событие «backlog разобран»: по нему
    пересчитывается дедупликация (scripts/dedup_stats.py)."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 10, shard="Алатауский 2к")
    record_sweep_run(
        {"started_at": "2026-08-01 04:00:00", "deal": "prodazha", "mode": "drain"}, db
    )
    client = FakeClient(_pages_by_shard({"Алатауский 2к": [1001]}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, db_path=db)

    assert stats["mode"] == "steady"
    assert stats["drain_completed"] is True


# ---------- откат по двум подряд банам (приёмочный критерий) ----------


class _BannedClient(FakeClient):
    def get(self, url: str) -> str | None:
        self.requested.append(url)
        raise BanDetected("3 URL подряд получили только HTTP 403")


def test_two_consecutive_bans_rollback_to_steady_delays(tmp_path, monkeypatch):
    """Приёмочный критерий: два подряд BanDetected возвращают задержки в
    steady state и поднимают флаг. Разгон, который поймал бан и продолжает
    разгоняться, хуже отсутствия разгона: потеряем и день, и IP."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 5_001, shard="Алатауский 2к")  # режим drain
    monkeypatch.setattr(rescrape, "_alert_ban", lambda exc: None)
    rollback_alerts: list[int] = []
    monkeypatch.setattr(
        rescrape, "_alert_ban_rollback", lambda streak: rollback_alerts.append(streak)
    )

    # Два подряд прохода с баном.
    client = _BannedClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    for _ in range(2):
        stats = sweep(max_pages=1, db_path=db)
        assert stats["banned"] is True
    assert rollback_alerts == [2]  # алерт ровно на фронте серии

    # Третий проход: здоров, но идёт на steady-паузах, несмотря на drain-режим.
    # (потолок 2 — чтобы не разгрести backlog ниже порога выхода из разгона)
    client = FakeClient(_pages_by_shard({"Алатауский 2к": [1001, 1002]}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)
    stats = sweep(max_pages=1, max_new_details=2, db_path=db)

    assert stats["mode"] == "drain"  # режим по backlog'у не отменяется
    assert stats["ban_rollback"] is True  # но паузы — откачены
    assert stats["delay_range"] == list(STEADY_MODE.delay_range)
    assert stats["ban_streak"] == 0  # чистый проход сбросил серию
    assert rollback_alerts == [2]  # повторного алерта нет

    # Следующий проход — серия сброшена, разгонные паузы вернулись.
    stats = sweep(max_pages=1, max_new_details=2, db_path=db)
    assert stats["ban_rollback"] is False
    assert stats["delay_range"] == list(DRAIN_MODE.delay_range)


# ---------- второй проход планировщика: интеграция (приёмочный критерий) ----------


def test_leftover_redistributed_from_shallow_to_deep_shard(tmp_path, monkeypatch):
    """Приёмочный критерий: остаток квоты от шарда с мелким backlog'ом
    перераспределяется замеренному шарду с глубоким; незамеренный шард с
    backlog'ом не получает ничего."""
    db = tmp_path / "test.db"
    init_db(db)
    # Донор «Алатауский 2к»: 60 лотов в выдаче, но 58 из них уже с деталями
    # (известны) — backlog мелкий (2 sighting'а).
    with get_conn(db) as conn:
        conn.executemany(
            "INSERT INTO listings (id, url, title, is_active) VALUES (?, ?, 't', 1)",
            [(1000 + i, f"https://krisha.kz/a/show/{1000 + i}") for i in range(58)],
        )
    record_listing_shards([(1000 + i, "Алатауский 2к") for i in range(58)], db)
    _seed_backlog(db, 2, shard="Алатауский 2к", start_id=1058)
    # Получатель «Медеуский 1к»: 40 лотов в выдаче, все — свежий backlog.
    # Незамеренный «Турксибский 1к»: backlog есть, выдачи нет — ничего не
    # получает (нет строки стока для сверки batch-TVD).
    _seed_backlog(db, 3, shard="Турксибский 1к", start_id=5000)

    stock = {
        "Алатауский 2к": list(range(1000, 1060)),   # 60 = 60% стока
        "Медеуский 1к": list(range(2000, 2040)),    # 40 = 40% стока
    }
    client = FakeClient(_pages_by_shard(stock))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, max_new_details=10, db_path=db)

    # Базовые квоты 6/4; у Алатауского backlog 2 < 6 — остаток 4 ушёл
    # Медеускому (замерен, backlog глубже квоты).
    assert stats["quota_redistributed"] == 4
    assert stats["detail_plan"] == {"Алатауский 2к": 6, "Медеуский 1к": 8}
    # И суммарная докачка достигла потолка: 2 + 8 = 10 (было бы 6 без
    # второго прохода — ровно та систематическая недоборка из issue).
    assert stats["details_fetched"] == 10
    with get_conn(db) as conn:
        turksib_detailed = conn.execute(
            "SELECT COUNT(*) FROM listings l JOIN listing_shards s ON s.listing_id = l.id "
            "WHERE l.title IS NOT NULL AND s.shard = 'Турксибский 1к'"
        ).fetchone()[0]
    assert turksib_detailed == 0  # незамеренному — ничего


def test_failed_shard_leftover_is_not_redistributed(tmp_path, monkeypatch):
    """Приёмочный критерий: остаток от СБОЙНОГО шарда (не покрыт этим
    проходом, квота по фолбэк-стоку) соседям НЕ раздаётся — инвариант #166
    сохраняется, его недобор компенсируется его курсором позже."""
    db = tmp_path / "test.db"
    init_db(db)
    # «Алатауский 2к» покрыт, backlog мелкий (2 при 58 детальных).
    with get_conn(db) as conn:
        conn.executemany(
            "INSERT INTO listings (id, url, title, is_active) VALUES (?, ?, 't', 1)",
            [(1000 + i, f"https://krisha.kz/a/show/{1000 + i}") for i in range(58)],
        )
    record_listing_shards([(1000 + i, "Алатауский 2к") for i in range(58)], db)
    _seed_backlog(db, 2, shard="Алатауский 2к", start_id=1058)
    # «Медеуский 1к» НЕ покрыт этим проходом, но имеет фолбэк-сток 40 с
    # прошлого прохода (строка sweep_shard_stats) и глубокий backlog.
    _seed_backlog(db, 10, shard="Медеуский 1к", start_id=2000)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO sweep_shard_stats (run_seq, shard, started_at, stock, quota, "
            "fetched, backlog_before, backlog_after, wrapped, pass_cap) "
            "VALUES (1, 'Медеуский 1к', '2026-08-01 04:00:00', 40, 4, 4, 10, 6, 0, 10)"
        )

    client = FakeClient(_pages_by_shard({"Алатауский 2к": list(range(1000, 1060))}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, max_new_details=10, db_path=db)

    # Квоты: Алатауский 6 (сток 60/100), Медеуский 4 (фолбэк-сток 40/100).
    assert stats["detail_plan"] == {"Алатауский 2к": 6, "Медеуский 1к": 4}
    # Остаток Алатауского (6−2=4) НЕ перераспределён: единственный глубокий
    # шард не замерен этим проходом.
    assert stats["quota_redistributed"] == 0
    # Алатауский докачал свои 2, Медеуский — свои 4 по фолбэк-квоте (её
    # никто не отбирал и не добавлял).
    assert stats["details_fetched"] == 2 + 4


# ---------- подрезка потолка по плану (приёмочный критерий) ----------


def test_plan_trims_cap_in_advance_using_history_timings(tmp_path, monkeypatch):
    """Приёмочный критерий: план прохода, не влезающий в дедлайн по
    историческим таймингам, урезает потолок заранее (до фазы докачки), а не
    упирается в дедлайн на середине.

    Нарратив двух проходов: первый оценивает деталь по фолбэку (~3.6 с) и
    честно упирается в дедлайн (замер 120 с/деталь записан в sweep_runs);
    второй уже ЗНАЕТ цену из истории и подрезает потолок заранее — дедлайна
    вообще не касается."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 100, shard="Алатауский 2к")
    client_pages = _pages_by_shard({"Алатауский 2к": list(range(1001, 1101))})
    clock = [0.0]
    monkeypatch.setattr(rescrape, "_now", lambda: clock[0])

    class ClockClient(FakeClient):
        def get(self, url):
            clock[0] += 120.0 if "/a/show/" in url else 0.01
            return super().get(url)

    client = ClockClient(client_pages)
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    # Проход 1: фолбэк-оценка (~3.6 с/деталь) говорит «влезает» — реальность
    # (120 с) опровергает: мягкий стоп по дедлайну, как раньше.
    stats1 = sweep(max_pages=1, max_new_details=8, db_path=db, time_budget_min=6)
    assert stats1["time_budget_hit"] is True
    assert stats1["plan_trimmed"] is None
    assert stats1["details_fetched"] == 3  # проверка перед запросом: 3×120 ≤ 360

    # Проход 2: тайминги измерены первым проходом → потолок урезан ЗАРАНЕЕ.
    clock[0] += 1000.0  # пауза между проходами — не часть бюджета
    stats2 = sweep(max_pages=1, max_new_details=8, db_path=db, time_budget_min=6)

    assert stats2["plan_trimmed"] is not None
    assert stats2["plan_trimmed"]["reason"] == "time"
    assert stats2["max_new_details"] == 2  # (360 − ~0 − 36 резерв) // 120
    assert stats2["details_fetched"] == 2
    assert stats2["time_budget_hit"] is False  # дедлайна не коснулись вовсе
    # тайминги обоих проходов записаны — оценка следующего прохода честнее
    with get_conn(db) as conn:
        rows = conn.execute(
            "SELECT search_pages, search_seconds, detail_requests, detail_seconds, "
            "delay_lo, delay_hi, mode FROM sweep_runs ORDER BY started_at"
        ).fetchall()
    # started_at — PK с секундной точностью: два быстрых прохода подряд
    # могут схлопнуться в одну строку (INSERT OR REPLACE). Важна последняя:
    pages, sec, dreq, dsec, lo, hi, mode = rows[-1]
    assert pages == 32 and sec is not None
    assert dreq == 2 and dsec == pytest.approx(240.0, abs=1.0)
    assert (lo, hi) == tuple(STEADY_MODE.delay_range)
    assert mode == "steady"  # backlog 100 < порога выхода из разгона


def test_politeness_ceiling_trims_huge_explicit_cap(tmp_path, monkeypatch):
    """Потолок вежливости — константа в коде: проход, который по плану
    пробивает min(10k/сутки, 0.5 rps × бюджет), режется до входа в фазу
    докачки, даже если потолок задан явно руками."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 20_000, shard="Алатауский 2к")
    # Ручное понижение пауз ниже разумного (0.5–1.0 с) — от такой
    # «оптимизации» потолок вежливости и обязан защищать.
    monkeypatch.setenv("KRISHA_DELAY_MIN", "0.5")
    monkeypatch.setenv("KRISHA_DELAY_MAX", "1.0")
    from krisha import config

    monkeypatch.setattr(config, "REQUEST_DELAY_RANGE", (0.5, 1.0))
    monkeypatch.setattr(rescrape, "REQUEST_DELAY_RANGE", (0.5, 1.0))
    client = FakeClient(_pages_by_shard({"Алатауский 2к": list(range(1001, 1101))}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", lambda html, url: None)

    stats = sweep(max_pages=1, max_new_details=20_000, db_path=db)

    # min(10000, 0.5 × 19200) − 32 страницы выдачи = 9568
    assert stats["plan_trimmed"]["reason"] == "politeness"
    assert stats["max_new_details"] == 9_568
    detail_requests = [u for u in client.requested if "/a/show/" in u]
    assert len(detail_requests) == 9_568  # потолок исполнен точно, без дедлайна
    assert stats["time_budget_hit"] is False


# ---------- диагностика без докачки: starved не поднимается (хвост #169) ----------


def test_no_starved_flag_when_cap_zero_and_streak_not_poisoned(tmp_path, monkeypatch):
    """Хвост ревью #169 (issue #152): при --max-new 0 флаг starved_shards не
    поднимается (все 32 шарда с quota=0 — это режим прохода, а не заморозка),
    и диагностические строки (pass_cap=0) не наращивают и не обрывают серии
    для последующих обычных проходов."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 2, shard="Алатауский 2к")  # шард никогда не покрыт
    client = FakeClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    # Три диагностических прохода подряд: флага нет ни разу.
    for _ in range(STARVED_SHARD_STREAK):
        stats = sweep(max_pages=1, max_new_details=0, db_path=db)
        assert stats["starved_shards"] == []
    with get_conn(db) as conn:
        caps = conn.execute("SELECT DISTINCT pass_cap FROM sweep_shard_stats").fetchall()
    assert [r[0] for r in caps] == [0]  # строки есть (сток), но потолок 0

    # Теперь обычные проходы: серия для «Алатауский 2к» начинается с НУЛЯ —
    # диагностика её не накопила; флаг — ровно на STARVED_SHARD_STREAK-й.
    for pass_no in range(1, STARVED_SHARD_STREAK):
        stats = sweep(max_pages=1, max_new_details=5, db_path=db)
        assert stats["starved_shards"] == [], (pass_no, stats["starved_shards"])
    stats = sweep(max_pages=1, max_new_details=5, db_path=db)
    assert stats["starved_shards"] == ["Алатауский 2к"]


# ---------- сентинел миграции бэкфилла — по факту выполнения (хвост #169) ----------


def test_backfill_sentinel_only_after_actual_backfill(tmp_path, monkeypatch):
    """Хвост ревью #169 (issue #152): бэкфилл, не исполнившийся на
    легаси-схеме (нет title/district/rooms), НЕ ставит сентинел: следующий
    init_db (когда схему дотащат миграции) обязан попробовать снова. Раньше
    миграция помечалась сделанной навсегда, не сделавшись."""
    import sqlite3

    from krisha import db as db_mod

    # Прямая проверка контракта: на голой легаси-таблице бэкфилл честно
    # отвечает None («не смог»), а не 0 («исполнил, нечего было»).
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute(
            "CREATE TABLE listings (id INTEGER PRIMARY KEY, url TEXT, price INTEGER)"
        )
    assert db_mod.backfill_listing_shards_from_details(legacy) is None

    # Сквозное поведение init_db: ранний выход → сентинела нет; повторный
    # init_db с исполнимым бэкфиллом → сентинел И реальная атрибуция.
    db = tmp_path / "test.db"
    monkeypatch.setattr(
        db_mod, "backfill_listing_shards_from_details", lambda **_kw: None
    )
    init_db(db)
    with get_conn(db) as conn:
        assert conn.execute(
            "SELECT key FROM sweep_state WHERE key LIKE 'migrate:%'"
        ).fetchall() == []

    monkeypatch.undo()
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, title, district, rooms, is_active) "
            "VALUES (1, 'u', 't', 'Bostandykskiy_r-n', 2, 1)"
        )
    init_db(db)
    with get_conn(db) as conn:
        sentinels = conn.execute(
            "SELECT key FROM sweep_state WHERE key LIKE 'migrate:%'"
        ).fetchall()
        shard = conn.execute(
            "SELECT shard FROM listing_shards WHERE listing_id = 1"
        ).fetchone()
    assert [r[0] for r in sentinels] == ["migrate:listing_shards_backfill:v1"]
    assert shard[0] == "Бостандыкский 2к"  # и бэкфилл реально исполнился



# ---------- наблюдаемость: unattributed и тайминги в итогах ----------


def test_unattributed_backlog_reported_in_stats(tmp_path, monkeypatch):
    """Хвост ревью #169 (issue #152): unattributed_backlog едет в итоги
    прохода (и дальше в утренний отчёт) — второй способ тихой заморозки
    должен быть виден так же, как starved_shards."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 3)  # без атрибуции
    _seed_backlog(db, 2, shard="Алатауский 2к", start_id=800_000)
    client = FakeClient({})
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)

    stats = sweep(max_pages=1, max_new_details=5, db_path=db)

    assert stats["unattributed_backlog"] == 3
    assert stats["detail_queue_before"] == 5


def test_empty_env_delay_is_not_an_override(tmp_path, monkeypatch):
    """Воркфлоу выставляет env плейсхолдером input'а — без значения это
    пустая строка. Пустой input = «используй пресет режима», иначе пресет
    в CI не сработает никогда (а до правки config.py импорт просто падал
    на float(""))."""
    db = tmp_path / "test.db"
    init_db(db)
    _seed_backlog(db, 5, shard="Алатауский 2к")
    monkeypatch.setenv("KRISHA_DELAY_MIN", "")
    monkeypatch.setenv("KRISHA_DELAY_MAX", "   ")
    client = FakeClient(_pages_by_shard({"Алатауский 2к": [1001]}))
    monkeypatch.setattr(rescrape, "PoliteClient", lambda **_kw: client)
    monkeypatch.setattr(rescrape, "parse_detail", _parse_detail_by_url)

    stats = sweep(max_pages=1, db_path=db)

    assert stats["delay_range"] == list(STEADY_MODE.delay_range)  # пресет, не env


def test_config_tolerates_empty_delay_env(monkeypatch):
    import importlib

    from krisha import config

    monkeypatch.setenv("KRISHA_DELAY_MIN", "")
    monkeypatch.setenv("KRISHA_DELAY_MAX", " ")
    try:
        importlib.reload(config)
        assert config.REQUEST_DELAY_RANGE == (2.0, 4.0)
        monkeypatch.setenv("KRISHA_DELAY_MIN", "1.5")
        importlib.reload(config)
        assert config.REQUEST_DELAY_RANGE == (1.5, 4.0)
    finally:
        monkeypatch.undo()
        importlib.reload(config)  # вернуть модуль в дефолт для остальных тестов
