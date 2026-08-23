"""Учение по восстановлению базы из релиза (проверки самого учения).

Бэкап, который ни разу не разворачивали, бэкапом не является — но и учение,
которое всегда зелёное, бесполезно: тесты проверяют, что проверки реально
падают на битых данных.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "restore_drill", ROOT / "scripts" / "restore_drill.py"
)
restore_drill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(restore_drill)


def _db(tmp_path, *, listings=1, active=1, history=1, last_seen="2026-08-24 04:00:00"):
    from krisha.db import init_db

    path = tmp_path / "krisha.db"
    init_db(path)
    con = sqlite3.connect(path)
    for i in range(listings):
        con.execute(
            "INSERT INTO listings (id, url, price, first_seen, last_seen, is_active) "
            "VALUES (?, 'u', 1, ?, ?, ?)",
            (i + 1, last_seen, last_seen, 1 if i < active else 0),
        )
    for _ in range(history):
        con.execute(
            "INSERT INTO price_history (listing_id, price, observed_at) VALUES (1, 1, ?)",
            (last_seen,),
        )
    con.commit()
    con.close()
    return path


def test_drill_passes_on_healthy_database(tmp_path):
    path = _db(tmp_path)

    summary = restore_drill.check_database(path, max_age_hours=10**6)

    assert summary["listings"] == 1 and summary["active"] == 1


def test_drill_fails_on_empty_database(tmp_path):
    path = _db(tmp_path, listings=0, active=0, history=0)

    with pytest.raises(restore_drill.DrillError):
        restore_drill.check_database(path, max_age_hours=10**6)


def test_drill_fails_on_stale_database(tmp_path):
    """Скачался и распаковался — ещё не значит «данные живые»."""
    path = _db(tmp_path, last_seen="2020-01-01 00:00:00")

    with pytest.raises(restore_drill.DrillError):
        restore_drill.check_database(path, max_age_hours=48)


def test_age_hours_parses_both_time_formats():
    assert restore_drill._age_hours("2026-08-24 04:00:00") is not None
    assert restore_drill._age_hours("2026-08-24 04:00:00.123") is not None
    assert restore_drill._age_hours(None) is None
    assert restore_drill._age_hours("мусор") is None
