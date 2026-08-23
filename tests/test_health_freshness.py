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
