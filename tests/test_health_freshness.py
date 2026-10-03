from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from krisha import db
from krisha.api import app as app_module
from krisha.api.app import app
from krisha.db import get_conn

NOW = datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)


def _seed_listing(db_path, *, last_seen: datetime) -> None:
    db.init_db(db_path)
    db.upsert_listing(
        {
            "id": 9001,
            "url": "https://krisha.kz/a/show/9001",
            "title": "Свежий лот",
            "price": 42_000_000,
            "area": 55.0,
            "rooms": 2,
            "district": "Auezovskiy_r-n",
            "source": "test",
        },
        db_path=db_path,
    )
    observed = last_seen.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    with get_conn(db_path) as conn:
        conn.execute("UPDATE listings SET last_seen = ?, scraped_at = ? WHERE id = 9001", (observed, observed))


def _health_json(monkeypatch, db_path):
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    monkeypatch.setattr(app_module, "_utcnow", lambda: NOW)
    return TestClient(app).get("/api/health").json()


def test_health_marks_recent_database_fresh(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    _seed_listing(db_path, last_seen=NOW - timedelta(hours=2))

    data = _health_json(monkeypatch, db_path)

    assert data["freshness"] == "ok"
    assert data["data_age_hours"] == pytest.approx(2.0, abs=0.02)


def test_health_marks_database_stale_after_30_hours(tmp_path, monkeypatch):
    db_path = tmp_path / "stale.db"
    _seed_listing(db_path, last_seen=NOW - timedelta(hours=31, minutes=30))

    data = _health_json(monkeypatch, db_path)

    assert data["freshness"] == "stale"
    assert data["data_age_hours"] == pytest.approx(31.5, abs=0.02)


def test_health_marks_missing_real_observations_stale(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    db.init_db(db_path)

    data = _health_json(monkeypatch, db_path)

    assert data["freshness"] == "stale"
    assert data["data_age_hours"] is None


# --- Ревизия сборки (гейт смоука после деплоя) -----------------------------

def test_health_reports_build_revision(tmp_path, monkeypatch):
    """Смоук после деплоя обязан отличать новый контейнер от старого.

    Старый Space отвечает 200 всю пересборку, поэтому «сервис жив» ничего не
    доказывает: без ревизии прогон подтверждал предыдущий деплой.
    """
    db_path = tmp_path / "revision.db"
    _seed_listing(db_path, last_seen=NOW - timedelta(hours=1))
    monkeypatch.setattr(app_module, "BUILD_REVISION", "a" * 40)

    data = _health_json(monkeypatch, db_path)

    assert data["revision"] == "a" * 40


def test_build_revision_reads_deploy_file(tmp_path, monkeypatch):
    """Файл кладёт deploy-hf.yml в data/ — Dockerfile копирует только его."""
    monkeypatch.delenv("BUILD_REVISION", raising=False)
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    (tmp_path / "build_revision.txt").write_text("deadbeef\n", encoding="utf-8")

    assert app_module._build_revision() == "deadbeef"


def test_build_revision_is_none_without_deploy_file(tmp_path, monkeypatch):
    """Локальный запуск: ревизии нет, и это не ошибка."""
    monkeypatch.delenv("BUILD_REVISION", raising=False)
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)

    assert app_module._build_revision() is None


# --- Свежесть по сборщику, а не по любым записям в базе -----------------------

def _db_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def test_user_check_does_not_make_stale_data_fresh(tmp_path, monkeypatch):
    """Проверка ссылки пишет лот с last_seen = now. Раньше это поднимало
    MAX(last_seen), и сломанный ночной сбор выглядел как «обновлено только что»."""
    db_path = tmp_path / "masked.db"
    _seed_listing(db_path, last_seen=NOW - timedelta(hours=40))
    db.upsert_listing(
        {"id": 9002, "url": "https://krisha.kz/a/show/9002", "title": "Проверил пользователь",
         "price": 30_000_000, "area": 50.0, "rooms": 2, "source": "user"},
        db_path=db_path,
    )
    with get_conn(db_path) as conn:  # и поверх строки сборщика — тоже user-upsert
        conn.execute("UPDATE listings SET last_seen = ? WHERE id IN (9001, 9002)", (_db_time(NOW),))
        conn.execute("UPDATE listings SET source = 'user' WHERE id = 9002")
        conn.execute(
            "INSERT INTO sweep_runs (started_at, deal, search_seconds, detail_seconds) VALUES (?, ?, ?, ?)",
            (_db_time(NOW - timedelta(hours=45)), "prodazha", 3600.0, 3600.0),
        )

    data = _health_json(monkeypatch, db_path)

    assert data["freshness"] == "stale"
    assert data["data_age_hours"] == pytest.approx(43.0, abs=0.02)  # конец прохода: старт + 2 ч


def test_freshness_counts_from_the_end_of_the_last_sweep(tmp_path, monkeypatch):
    db_path = tmp_path / "sweep.db"
    _seed_listing(db_path, last_seen=NOW - timedelta(hours=1))
    with get_conn(db_path) as conn:
        conn.executemany(
            "INSERT INTO sweep_runs (started_at, deal, search_seconds, detail_seconds) VALUES (?, ?, ?, ?)",
            [(_db_time(NOW - timedelta(hours=30)), "prodazha", 9000.0, 9000.0),
             (_db_time(NOW - timedelta(hours=6)), "prodazha", 9000.0, 9000.0)],
        )

    data = _health_json(monkeypatch, db_path)

    assert data["freshness"] == "ok"
    assert data["data_age_hours"] == pytest.approx(1.0, abs=0.02)  # 6 ч назад старт, 5 ч проход
