"""issue #156: разовая ретро-разметка когорты осиротевшего восстановительного прохода."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backfill_gap_cohort import backfill  # noqa: E402

from krisha.db import get_conn, init_db  # noqa: E402

WINDOW_START = "2026-07-26 16:04:00"
WINDOW_END = "2026-07-26 18:01:00"


def _rent_like_db(tmp_path):
    """База в состоянии, в котором оказалась аренда после прохода 26.07.

    Проход шёл на коде без механизма когорт: волна получила first_seen 26.07
    без метки, а те, кого не нашли, уехали в delisted, сохранив last_seen от
    13.07 (delist его не трогает).
    """
    db = tmp_path / "rent.db"
    init_db(db)
    with get_conn(db) as conn:
        # Жили до провала, найдены проходом 26.07 — last_seen обновился.
        for i in range(40):
            conn.execute(
                "INSERT INTO listings (id, url, price, area, title, is_active, "
                "first_seen, last_seen) VALUES (?, 'u', 300000, 60.0, 't', 1, "
                "'2026-06-20 10:00:00', '2026-07-26 17:30:00')",
                (1000 + i,),
            )
        # Не нашлись — сняты этим же проходом, last_seen остался от 13.07.
        for i in range(30):
            conn.execute(
                "INSERT INTO listings (id, url, price, area, title, is_active, "
                "first_seen, last_seen, delisted_at) VALUES (?, 'u', 300000, 60.0, "
                "'t', 0, '2026-06-20 10:00:00', '2026-07-13 19:18:53', "
                "'2026-07-26 17:59:00')",
                (2000 + i,),
            )
        # Волна: впервые увидены этим проходом, метки нет.
        for i in range(50):
            conn.execute(
                "INSERT INTO listings (id, url, price, area, title, is_active, "
                "first_seen, last_seen) VALUES (?, 'u', 300000, 60.0, 't', 1, "
                "'2026-07-26 16:40:00', '2026-07-26 17:30:00')",
                (3000 + i,),
            )
    return db


def test_marks_the_wave_and_records_the_gap(tmp_path):
    db = _rent_like_db(tmp_path)

    result = backfill(str(db), WINDOW_START, WINDOW_END, expect=50)

    assert result["marked"] == 50
    # gap_start выведен из базы: последнее наблюдение до окна — это last_seen
    # снятых лотов, который delist не трогал.
    assert result["gap_start"] == "2026-07-13 19:18:53"
    assert result["cohort"] == "gap:2026-07-13"

    with get_conn(db) as conn:
        gaps = conn.execute("SELECT gap_start, gap_end, note FROM data_gaps").fetchall()
        assert len(gaps) == 1 and "retro" in gaps[0][2]
        wave = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE first_seen_cohort = 'gap:2026-07-13'"
        ).fetchone()[0]
        assert wave == 50
        # Лоты, жившие до провала, когортой бэкфилла не помечены.
        old = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE id < 3000 "
            "AND first_seen_cohort LIKE 'gap:%'"
        ).fetchone()[0]
        assert old == 0


def test_is_idempotent(tmp_path):
    """Повторный запуск ничего не меняет — предикат IS NULL и INSERT OR IGNORE."""
    db = _rent_like_db(tmp_path)
    backfill(str(db), WINDOW_START, WINDOW_END, expect=50)

    second = backfill(str(db), WINDOW_START, WINDOW_END)

    assert second["marked"] == 0
    assert second["candidates"] == 0
    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM data_gaps").fetchone()[0] == 1


def test_expect_mismatch_aborts_without_writing(tmp_path):
    """Защита от запуска по чужой базе или с неверным окном.

    Молча пометить не ту когорту хуже, чем не пометить: метка ставится один
    раз (предикат IS NULL) и снять её потом нечем.
    """
    db = _rent_like_db(tmp_path)

    with pytest.raises(SystemExit, match="ОЖИДАЛОСЬ"):
        backfill(str(db), WINDOW_START, WINDOW_END, expect=9999)

    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM data_gaps").fetchone()[0] == 0
        marked = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE first_seen_cohort LIKE 'gap:%'"
        ).fetchone()[0]
        assert marked == 0


def test_dry_run_writes_nothing(tmp_path):
    db = _rent_like_db(tmp_path)

    result = backfill(str(db), WINDOW_START, WINDOW_END, expect=50, dry_run=True)

    assert result["candidates"] == 50 and result["marked"] == 0
    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM data_gaps").fetchone()[0] == 0


def test_refuses_when_window_precedes_all_observations(tmp_path):
    """Окно раньше любых наблюдений — значит оно задано неверно, а не что
    провал был бесконечным. Выводить gap_start неоткуда."""
    db = tmp_path / "empty.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, area, title, is_active, "
            "first_seen, last_seen) VALUES (1, 'u', 300000, 60.0, 't', 1, "
            "'2026-07-26 16:40:00', '2026-07-26 17:30:00')"
        )

    with pytest.raises(SystemExit, match="Не нашлось"):
        backfill(str(db), WINDOW_START, WINDOW_END)
