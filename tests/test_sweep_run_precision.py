"""issue #171: два прохода в одну секунду не имеют права схлопываться в одну
строку истории.

sweep_runs.started_at — PRIMARY KEY с INSERT OR REPLACE. Пока время писалось
секундной точностью, ручной перезапуск джобы (или шардированный прогон) молча
стирал предыдущую строку. На этой истории считаются инварианты дневного отчёта
(«очередь растёт третий проход подряд», «упёрлись в потолок N дней подряд») —
потерянная строка ломает именно их.
"""

import re
import sqlite3

from krisha.db import get_conn, init_db, recent_sweep_runs, record_sweep_run

MS_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")


def test_two_passes_in_the_same_second_are_kept(tmp_path):
    db = tmp_path / "sweep.db"
    init_db(db)

    record_sweep_run({"started_at": "2026-08-24 04:00:00.100", "deal": "prodazha",
                      "discovered_new": 10}, db)
    record_sweep_run({"started_at": "2026-08-24 04:00:00.900", "deal": "prodazha",
                      "discovered_new": 20}, db)

    runs = recent_sweep_runs(limit=5, db_path=db)
    assert [r["discovered_new"] for r in runs] == [20, 10]


def test_repeat_write_of_the_same_pass_still_replaces(tmp_path):
    """Идемпотентность записи одного и того же прохода сохраняется."""
    db = tmp_path / "sweep.db"
    init_db(db)

    record_sweep_run({"started_at": "2026-08-24 04:00:00.100", "discovered_new": 10}, db)
    record_sweep_run({"started_at": "2026-08-24 04:00:00.100", "discovered_new": 11}, db)

    runs = recent_sweep_runs(limit=5, db_path=db)
    assert [r["discovered_new"] for r in runs] == [11]


def test_pass_timestamp_has_milliseconds(tmp_path):
    """Тот же SQL, что берёт проход в rescrape.sweep."""
    db = tmp_path / "sweep.db"
    init_db(db)

    with get_conn(db) as conn:
        value = conn.execute("SELECT strftime('%Y-%m-%d %H:%M:%f', 'now')").fetchone()[0]

    assert MS_TIME.match(value), value


def test_old_second_precision_rows_sort_correctly_next_to_new_ones(tmp_path):
    """История смешанная: старые строки без долей секунды, новые с ними.

    Сортировка в recent_sweep_runs текстовая, поэтому важно, что
    «2026-08-24 04:00:00» < «2026-08-24 04:00:00.100» и как строки тоже.
    """
    db = tmp_path / "sweep.db"
    init_db(db)

    record_sweep_run({"started_at": "2026-08-23 04:00:00", "discovered_new": 1}, db)
    record_sweep_run({"started_at": "2026-08-24 04:00:00", "discovered_new": 2}, db)
    record_sweep_run({"started_at": "2026-08-24 04:00:00.100", "discovered_new": 3}, db)

    runs = recent_sweep_runs(limit=5, db_path=db)
    assert [r["discovered_new"] for r in runs] == [3, 2, 1]


def test_cohort_marking_compares_like_with_like(tmp_path):
    """listings.first_seen пишется через datetime('now') — без долей секунды.

    Пометка когорты сравнивает его со стартом прохода, поэтому проход обязан
    сравнивать секундной формой своего времени: иначе лоты, созданные в
    стартовую секунду, выпадут из пометки («…:30» < «…:30.123» как строки).
    """
    db = tmp_path / "sweep.db"
    init_db(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO listings (id, url, price, first_seen, last_seen, is_active) "
        "VALUES (1, 'u', 1, '2026-08-24 04:00:00', '2026-08-24 04:00:00', 1)"
    )
    con.commit()
    con.close()

    pass_started_at = "2026-08-24 04:00:00.500"
    pass_started_at_sec = pass_started_at[:19]

    with get_conn(db) as conn:
        missed = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE first_seen >= ?", (pass_started_at,)
        ).fetchone()[0]
        caught = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE first_seen >= ?", (pass_started_at_sec,)
        ).fetchone()[0]

    assert (missed, caught) == (0, 1)
