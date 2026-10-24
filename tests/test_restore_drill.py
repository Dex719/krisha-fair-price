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


def test_drill_freshness_uses_sweep_runs_like_prod(tmp_path):
    """Аудит 24.10: MAX(last_seen) двигают и пользовательские проверки — свежесть
    как у /api/health: конец последнего прохода сборщика (data_observed_at)."""
    from datetime import datetime, timezone

    from krisha.db import get_conn

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    path = _db(tmp_path, last_seen=now)
    with get_conn(path) as conn:
        conn.execute(
            "INSERT INTO sweep_runs (started_at, deal, found_in_search, search_seconds) "
            "VALUES ('2020-01-01 00:00:00', 'prodazha', 1, 60)"
        )
    with pytest.raises(restore_drill.DrillError, match="свежие"):
        restore_drill.check_database(path, max_age_hours=48)
    # у аренды прохода в sweep_runs нет — фолбэк на last_seen сборщика
    assert restore_drill.check_database(path, max_age_hours=48, deal="arenda")["active"] == 1


def test_restore_is_strict_about_checksum(monkeypatch, tmp_path):
    from krisha import db_release

    calls = []
    monkeypatch.setattr(db_release, "download", lambda *a, **kw: calls.append((a, kw)))
    restore_drill.restore(tmp_path / "a.db")
    restore_drill.restore(tmp_path / "b.db", rent=True)
    assert all(kw.get("strict_checksum") is True for _, kw in calls)
    assert calls[1][0][1] == db_release.RENT_ASSET_NAME


def test_drill_fails_when_sha_is_missing(monkeypatch, tmp_path, capsys):
    from krisha import db_release

    def no_sha(*a, **kw):
        raise db_release.ChecksumUnavailable("нет ассета .sha256")

    monkeypatch.setattr(db_release, "download", no_sha)
    assert restore_drill.main(["--rent", "--skip-app"]) == 1
    assert ".sha256" in capsys.readouterr().err


def test_age_hours_parses_both_time_formats():
    assert restore_drill._age_hours("2026-08-24 04:00:00") is not None
    assert restore_drill._age_hours("2026-08-24 04:00:00.123") is not None
    assert restore_drill._age_hours(None) is None
    assert restore_drill._age_hours("мусор") is None
