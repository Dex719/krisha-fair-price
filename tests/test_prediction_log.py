"""Долговечный лог предиктов (#128): в SQLite он не переживает ночную пересборку."""

import json

import pytest

from krisha import db, prediction_log


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(prediction_log, "LOG_PATH", tmp_path / "prediction_log.json")
    monkeypatch.setattr(prediction_log, "_state", None)
    monkeypatch.setattr(prediction_log, "_last_flush", None)
    monkeypatch.setenv("USAGE_FLUSH_SYNC", "1")  # без фонового потока
    monkeypatch.setenv("PREDICTION_LOG", "1")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def _rows(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_record_writes_row_to_file(tmp_path):
    prediction_log.record(123, 40_000_000.0, 36_000_000.0, 44_000_000.0, "FAIR", "2026-08-23")

    rows = _rows(prediction_log.LOG_PATH)
    assert len(rows) == 1
    row = next(iter(rows.values()))
    assert row["listing_id"] == 123
    assert row["verdict"] == "FAIR"
    assert row["fair_price"] == 40_000_000.0
    assert row["model"] == "2026-08-23"


def test_state_is_flat_dict_so_workers_merge_instead_of_overwriting():
    """Слияние конкурентных записей в subscriptions._merge_remote работает по
    ключам ВЕРХНЕГО уровня. Вложенный {"rows": [...]} второй воркер затирал бы
    целиком — ровно та потеря данных, от которой этот лог и заводится."""
    from krisha.subscriptions import _merge_remote

    prediction_log.record(1, 1.0, None, None, "FAIR", "m")
    mine = _rows(prediction_log.LOG_PATH)
    theirs = {"2026-08-01T00:00:00.000+00:00|999": {"listing_id": 999}}

    merged = _merge_remote(mine, theirs, None)

    assert len(merged) == 2


def test_disabled_in_github_actions(monkeypatch):
    """Пакетные предикты алертов (сотни за прогон) в git-лог не идут."""
    monkeypatch.setenv("PREDICTION_LOG", "auto")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_PAT", "x")

    prediction_log.record(1, 1.0, None, None, "FAIR", "m")

    assert not prediction_log.LOG_PATH.exists()


def test_disabled_by_default(monkeypatch):
    """Каждая запись — коммит в репозиторий, а любой коммит вне paths-ignore
    пересобирает Space. Пока файл не внесён в paths-ignore, лог включается
    только явной переменной PREDICTION_LOG=1."""
    monkeypatch.delenv("PREDICTION_LOG", raising=False)

    prediction_log.record(1, 1.0, None, None, "FAIR", "m")

    assert not prediction_log.LOG_PATH.exists()


def test_prune_keeps_newest_rows(monkeypatch):
    monkeypatch.setattr(prediction_log, "MAX_ROWS", 3)

    state = {f"2026-08-0{i}T00:00:00.000+00:00|{i}": {"listing_id": i} for i in range(1, 6)}
    prediction_log._prune(state)

    assert sorted(state) == [
        "2026-08-03T00:00:00.000+00:00|3",
        "2026-08-04T00:00:00.000+00:00|4",
        "2026-08-05T00:00:00.000+00:00|5",
    ]


def test_log_prediction_feeds_durable_log(tmp_path):  # PREDICTION_LOG=1 из фикстуры
    """Единая точка входа пишет и в базу, и в долговечный лог."""
    db_path = tmp_path / "krisha.db"
    db.init_db(db_path)

    db.log_prediction(777, 50e6, 45e6, 55e6, "OVERPRICED", "2026-08-23", db_path=db_path)

    rows = _rows(prediction_log.LOG_PATH)
    assert [r["listing_id"] for r in rows.values()] == [777]


def test_record_never_raises(monkeypatch):
    """Лог предикта не имеет права ронять оценку пользователю."""
    monkeypatch.setattr(prediction_log, "_record", _boom)

    prediction_log.record(1, 1.0, None, None, "FAIR", "m")  # не бросает


def _boom(*args, **kwargs):
    raise RuntimeError("боль")
