"""issue #154: вердикт ОК/ПРОБЛЕМА в начале утреннего отчёта.

Сбор данных был сломан 13 дней, и это не заметили. Причина не в отсутствии
алертов, а в том, что отчёт требовал читать таблицу цифр и сравнивать её с
ожиданием в голове — а глазу «новых 1000» выглядело ровно так же, как в
здоровый день.
"""

from krisha.daily_report import _verdict_lines
from krisha.db import init_db, record_sweep_run

HEALTHY = {
    "found_in_search": 39_000,
    "discovered_new": 900,
    "details_fetched": 300,
    "failed_shards": [],
}


def _run(db, started_at, *, queue, fetched=1000, cap=1000):
    record_sweep_run(
        {
            "started_at": started_at,
            "deal": "prodazha",
            "found_in_search": 39_000,
            "discovered_new": 900,
            "details_fetched": fetched,
            "max_new_details": cap,
            "detail_queue_after": queue,
            "price_changes": 300,
            "delisted": 400,
            "failed_shards": 0,
            "recovery_pass": 0,
            "suspicious": 0,
        },
        db,
    )


def test_healthy_pass_reports_ok(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    _run(db, "2026-07-20 04:00:00", queue=500, fetched=300)

    lines = _verdict_lines(HEALTHY, db, "sale")

    assert len(lines) == 1 and "ОК" in lines[0]


def test_dead_rescrape_is_caught(tmp_path):
    """Ровно то, что происходило 13 дней подряд: все шарды объявлялись
    капчей, выдача пуста, --fail-empty ронял джобу."""
    db = tmp_path / "t.db"
    init_db(db)

    lines = _verdict_lines(
        {
            "found_in_search": 0,
            "discovered_new": 0,
            "details_fetched": 0,
            "failed_shards": ["Алатауский 1к", "Алатауский 2к"],
        },
        db,
        "sale",
    )

    assert "ПРОБЛЕМА" in lines[0]
    body = "\n".join(lines)
    assert "в выдаче объявлений: 0" in body
    assert "шардов не покрыто: 2" in body


def test_cap_bound_collection_is_caught(tmp_path):
    """Слепота, из-за которой отставание сбора было невидимым: докачка
    двенадцать дней подряд упиралась в потолок 1000/день, а в отчёте это
    выглядело как «новых 1000», то есть как успех. Очередь при этом росла.
    """
    db = tmp_path / "t.db"
    init_db(db)
    for day, queue in ((21, 3_000), (22, 8_000), (23, 14_000), (24, 21_000)):
        _run(db, f"2026-07-{day} 04:00:00", queue=queue, fetched=1000, cap=1000)

    lines = _verdict_lines(
        {
            "found_in_search": 39_000,
            "discovered_new": 1000,
            "details_fetched": 1000,
            "failed_shards": [],
        },
        db,
        "sale",
    )

    body = "\n".join(lines)
    assert "ПРОБЛЕМА" in lines[0]
    assert "очередь деталей растёт" in body
    assert "упирается в потолок" in body


def test_shrinking_queue_is_not_flagged(tmp_path):
    """Обратная сторона: разгребание очереди не должно выглядеть проблемой,
    иначе после ускорения сбора отчёт будет красным каждый день."""
    db = tmp_path / "t.db"
    init_db(db)
    for day, queue in ((21, 21_000), (22, 14_000), (23, 8_000), (24, 3_000)):
        _run(db, f"2026-07-{day} 04:00:00", queue=queue, fetched=1000, cap=5000)

    lines = _verdict_lines(HEALTHY, db, "sale")

    assert len(lines) == 1 and "ОК" in lines[0]


def test_missing_summary_is_a_problem_not_silence(tmp_path):
    """Нет summary-JSON — значит рескрейп не доработал. Промолчать здесь
    означало бы вернуть ровно ту ситуацию, ради которой заведён вердикт."""
    db = tmp_path / "t.db"
    init_db(db)

    lines = _verdict_lines(None, db, "sale")

    assert "ПРОБЛЕМА" in lines[0]
    assert "счётчики прохода не найдены" in "\n".join(lines)


def test_starved_shards_are_caught(tmp_path):
    """issue #168: шард с непустым backlog'ом и нулевой квотой серией проходов
    — сбор по куску города молча остановился; до флага такое всплывало через
    две недели из model_meta. Здоровый проход флага не несёт."""
    db = tmp_path / "t.db"
    init_db(db)
    _run(db, "2026-08-03 04:00:00", queue=500, fetched=300)

    ok = _verdict_lines(HEALTHY, db, "sale")
    assert len(ok) == 1 and "ОК" in ok[0]

    lines = _verdict_lines(
        {**HEALTHY, "starved_shards": ["Алатауский 2к", "Медеуский 1к"]}, db, "sale"
    )

    assert "ПРОБЛЕМА" in lines[0]
    assert any("нулевой квотой" in line and "2" in line for line in lines)
