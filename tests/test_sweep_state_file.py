"""Аудит 24.10: серия банов и номер прохода переживают упавший проход."""

import json

from krisha.db import (
    advance_sweep_pass_seq,
    consecutive_bans,
    get_conn,
    init_db,
    record_consecutive_bans,
    sweep_pass_seq,
)
from krisha.scraping import sweep_state_file as ssf


def _set_at(db, key, at):
    with get_conn(db) as conn:
        conn.execute("UPDATE sweep_state SET updated_at = ? WHERE key = ?", (at, key))


def test_failed_pass_state_survives_via_file(tmp_path):
    """Сценарий из аудита: релизная база (последний удачный проход) — серия 0;
    упавший проход с баном записал серию 1 и сдвинул номер, но базу не залил.
    Файл состояния (always()) доносит это до следующего раннера."""
    released = tmp_path / "released.db"
    init_db(released)
    with get_conn(released) as conn:
        advance_sweep_pass_seq(conn, "prodazha")  # pass_seq = 1
        record_consecutive_bans(conn, "prodazha", 0)
    _set_at(released, "ban_streak:prodazha", "2026-10-20 01:00:00")

    # упавший проход: работал на копии той же базы
    failed = tmp_path / "failed.db"
    failed.write_bytes(released.read_bytes())
    with get_conn(failed) as conn:
        advance_sweep_pass_seq(conn, "prodazha")  # pass_seq = 2
        record_consecutive_bans(conn, "prodazha", 1)
    _set_at(failed, "ban_streak:prodazha", "2026-10-21 01:00:00")
    payload = ssf.export_state(failed, "prodazha")
    assert payload["keys"]["pass_seq"]["value"] == "2"
    assert payload["keys"]["ban_streak"]["value"] == "1"

    # следующий раннер скачал релизную базу и влил файл
    applied = ssf.import_state(released, "prodazha", json.loads(json.dumps(payload)))
    assert applied == {"pass_seq": "2", "ban_streak": "1"}
    with get_conn(released) as conn:
        assert sweep_pass_seq(conn, "prodazha") == 2
        assert consecutive_bans(conn, "prodazha") == 1


def test_stale_file_does_not_override_newer_db(tmp_path):
    db = tmp_path / "krisha.db"
    init_db(db)
    with get_conn(db) as conn:
        for _ in range(5):
            advance_sweep_pass_seq(conn, "arenda")
        record_consecutive_bans(conn, "arenda", 0)
    _set_at(db, "ban_streak:arenda", "2026-10-22 15:00:00")
    stale = {"deal": "arenda", "keys": {
        "pass_seq": {"value": "3", "updated_at": "2026-10-20 15:00:00"},
        "ban_streak": {"value": "2", "updated_at": "2026-10-20 15:00:00"},
    }}
    assert ssf.import_state(db, "arenda", stale) == {}
    with get_conn(db) as conn:
        assert sweep_pass_seq(conn, "arenda") == 5
        assert consecutive_bans(conn, "arenda") == 0


def test_cold_db_takes_file_values(tmp_path):
    db = tmp_path / "krisha.db"
    payload = {"deal": "prodazha", "keys": {
        "pass_seq": {"value": "17", "updated_at": "2026-10-22 01:00:00"},
        "ban_streak": {"value": "2", "updated_at": "2026-10-22 01:00:00"},
    }}
    ssf.import_state(db, "prodazha", payload)
    with get_conn(db) as conn:
        assert sweep_pass_seq(conn, "prodazha") == 17
        assert consecutive_bans(conn, "prodazha") == 2
        # инкремент продолжает с файла
        assert advance_sweep_pass_seq(conn, "prodazha") == 18


def test_wrong_deal_rejected(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        ssf.import_state(tmp_path / "x.db", "prodazha", {"deal": "arenda", "keys": {}})


def test_pull_without_asset_is_soft(tmp_path, monkeypatch):
    from krisha import db_release

    def missing(*a, **k):
        raise FileNotFoundError("нет ассета")

    monkeypatch.setattr(db_release, "_asset_source", missing)
    assert ssf.pull(tmp_path / "krisha.db", "prodazha") == {}


def test_cli_export_and_import_roundtrip(tmp_path):
    src = tmp_path / "a.db"
    init_db(src)
    with get_conn(src) as conn:
        advance_sweep_pass_seq(conn, "prodazha")
        advance_sweep_pass_seq(conn, "prodazha")
    out = tmp_path / "state.json"
    assert ssf.main(["export", "--deal", "prodazha", "--db", str(src), "--out", str(out)]) == 0
    dst = tmp_path / "b.db"
    assert ssf.main(["import", "--deal", "prodazha", "--db", str(dst), "--file", str(out)]) == 0
    with get_conn(dst) as conn:
        assert sweep_pass_seq(conn, "prodazha") == 2
