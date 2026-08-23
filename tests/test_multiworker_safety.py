"""Что ломается, когда воркеров становится больше одного (WEB_CONCURRENCY).

Три общих ресурса на два процесса: файл статистики, память о телеграм-
апдейтах и стартовая подготовка данных. Каждый из них здесь проверяется.
"""

from __future__ import annotations

import multiprocessing
import re
import threading
from datetime import datetime
from pathlib import Path

from krisha import usage
from krisha.api import app as app_module
from krisha.db import remember_update_id
from krisha.usage import ALMATY_TZ, merge_states

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


# --------------------------------------------------------------------------
# Статистика использования
# --------------------------------------------------------------------------
def test_second_worker_does_not_erase_first_workers_counters(monkeypatch, tmp_path):
    """Два процесса флашатся по очереди — счётчики не обнуляются."""
    path = tmp_path / "usage_stats.json"
    monkeypatch.setattr(usage, "USAGE_PATH", path)
    written: list[dict] = []
    monkeypatch.setattr(
        usage,
        "_flush",
        lambda state: written.append(usage.merge_states(usage.load_state(), state)),
    )

    now = datetime(2026, 8, 23, 12, 0, tzinfo=ALMATY_TZ)
    worker_a = {"days": {"2026-08-23": {"site": 40, "predict": 5, "bot": 0, "bot_users": ["aaa"], "hours": {"12": 45}}}}
    worker_b = {"days": {"2026-08-23": {"site": 12, "predict": 30, "bot": 2, "bot_users": ["bbb"], "hours": {"12": 44}}}}

    merged = merge_states(worker_a, worker_b)
    day = merged["days"]["2026-08-23"]

    assert day["site"] == 40
    assert day["predict"] == 30
    assert day["bot"] == 2
    assert sorted(day["bot_users"]) == ["aaa", "bbb"]
    assert day["hours"]["12"] == 45
    assert now  # день в ключе — тот же, что писали воркеры


def test_merge_keeps_days_from_both_sides():
    a = {"days": {"2026-08-22": {"site": 3}}}
    b = {"days": {"2026-08-23": {"site": 7}}}

    merged = merge_states(a, b)

    assert merged["days"]["2026-08-22"]["site"] == 3
    assert merged["days"]["2026-08-23"]["site"] == 7


def test_flush_does_not_block_the_request(monkeypatch, tmp_path):
    """Раз в полчаса кто-то платил секундами за поход в GitHub — больше нет."""
    monkeypatch.setattr(usage, "USAGE_PATH", tmp_path / "usage_stats.json")
    monkeypatch.setattr(usage, "_state", None)
    monkeypatch.setattr(usage, "_last_flush", None)
    monkeypatch.delenv("USAGE_FLUSH_SYNC", raising=False)
    started = threading.Event()
    release = threading.Event()

    def slow_flush(_state):
        started.set()
        release.wait(5)

    monkeypatch.setattr(usage, "_flush", slow_flush)

    usage.record_event("site")  # первое событие всегда флашится

    assert started.wait(5), "флаш должен был начаться в фоне"
    # запрос уже вернулся, хотя «сеть» ещё висит
    release.set()


# --------------------------------------------------------------------------
# Дедуп телеграм-апдейтов
# --------------------------------------------------------------------------
def test_update_id_is_remembered_across_processes(tmp_path):
    db_path = tmp_path / "tg.db"

    assert remember_update_id(555, db_path) is True
    assert remember_update_id(555, db_path) is False  # «второй воркер»
    assert remember_update_id(556, db_path) is True


def test_update_dedup_table_does_not_grow_forever(tmp_path):
    db_path = tmp_path / "tg.db"
    for i in range(60):
        remember_update_id(i, db_path, keep=10)

    from krisha.db import get_conn

    with get_conn(db_path) as conn:
        left = conn.execute("SELECT COUNT(*) FROM tg_updates").fetchone()[0]
    assert left <= 11


def test_missing_database_does_not_silence_the_bot(tmp_path):
    """Нет базы — дедуп деградирует, но апдейт обрабатывается."""
    assert remember_update_id(1, tmp_path / "нет.db") is True


# --------------------------------------------------------------------------
# Стартовая подготовка
# --------------------------------------------------------------------------
def _hold_lock(path: str, ready, done) -> None:  # pragma: no cover — дочерний процесс
    import fcntl

    with open(path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        ready.set()
        done.wait(10)


def test_startup_waits_for_the_worker_that_prepares_data(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "krisha.db")
    lock_path = tmp_path / ".startup.lock"
    ctx = multiprocessing.get_context("fork")
    ready, done = ctx.Event(), ctx.Event()
    holder = ctx.Process(target=_hold_lock, args=(str(lock_path), ready, done))
    holder.start()
    try:
        assert ready.wait(10)
        entered = threading.Event()

        def enter():
            with app_module._startup_lock():
                entered.set()

        thread = threading.Thread(target=enter)
        thread.start()
        assert not entered.wait(0.5), "второй воркер не должен входить, пока первый готовит данные"
        done.set()
        assert entered.wait(10), "после освобождения блокировки старт должен продолжиться"
        thread.join(5)
    finally:
        done.set()
        holder.join(10)


def test_dockerfile_runs_several_workers():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"--workers \$\{WEB_CONCURRENCY", text), "воркеры задаются переменной"
    assert "ENV WEB_CONCURRENCY=" in text
