"""Тесты ежедневного админ-отчёта и health-check скрипта."""

import importlib.util
import json
import sqlite3
from pathlib import Path

from krisha import daily_report
from krisha.daily_report import build_daily_report, send_daily_report


def _make_db(path: Path, active: int = 3, inactive: int = 1) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE listings (id INTEGER PRIMARY KEY, is_active INTEGER)")
        for i in range(active):
            conn.execute("INSERT INTO listings VALUES (?, 1)", (i,))
        for i in range(inactive):
            conn.execute("INSERT INTO listings VALUES (?, 0)", (100 + i,))


def _summary(path: Path, **overrides) -> Path:
    stats = {
        "found_in_search": 40000,
        "known_seen": 39000,
        "new_listings": 500,
        "price_changes": 120,
        "delisted": 300,
        "failed_shards": [],
    }
    stats.update(overrides)
    path.write_text(json.dumps(stats), encoding="utf-8")
    return path


def test_build_sale_report(monkeypatch, tmp_path):
    db = tmp_path / "krisha.db"
    _make_db(db)
    monkeypatch.setitem(daily_report._SCOPES, "sale", ("🌅 Утренний отчёт: продажа", db, tmp_path / "none.json"))
    summary = _summary(tmp_path / "stats.json")

    text = build_daily_report("sale", summary)

    assert "Утренний отчёт" in text
    assert "в выдаче <b>40000</b>" in text
    assert "новых <b>500</b>" in text
    assert "активных <b>3</b> (всего 4)" in text
    assert "Модель: v" in text  # meta лежит в репо


def test_failed_shards_flagged(monkeypatch, tmp_path):
    monkeypatch.setitem(daily_report._SCOPES, "rent", ("🌆 Вечерний отчёт: аренда", tmp_path / "no.db", tmp_path / "none.json"))
    summary = _summary(tmp_path / "stats.json", delisted=None, failed_shards=["almaty-1"])

    text = build_daily_report("rent", summary)

    assert "детект снятий: n/a" in text
    assert "снято None" not in text
    assert "Сбойные шарды: almaty-1" in text
    assert "Модель" not in text  # для аренды блок модели не показываем


def test_starved_shards_flagged(monkeypatch, tmp_path):
    """issue #168: замороженные для докачки шарды видны в отчёте поимённо —
    «шард без квоты» без района неотличим от шума."""
    monkeypatch.setitem(daily_report._SCOPES, "sale", ("🌅 Утренний отчёт: продажа", tmp_path / "no.db", tmp_path / "none.json"))
    summary = _summary(tmp_path / "stats.json", starved_shards=["Алатауский 2к"])

    text = build_daily_report("sale", summary)

    assert "нулевой квотой" in text
    assert "Алатауский 2к" in text


def test_suspicious_parse_rate_flagged(monkeypatch, tmp_path):
    """issue #97: проход помечен suspicious → в отчёте видно алерт с медианой."""
    monkeypatch.setitem(
        daily_report._SCOPES, "rent", ("🌆 Вечерний отчёт: аренда", tmp_path / "no.db", tmp_path / "none.json")
    )
    summary = _summary(
        tmp_path / "stats.json",
        found_in_search=8000,
        suspicious=True,
        parse_rate_median_7=40000,
    )

    text = build_daily_report("rent", summary)

    assert "Parse-rate просел" in text
    assert "8000" in text
    assert "40000" in text


def test_suspicious_active_in_db_baseline_flagged(monkeypatch, tmp_path):
    """issue #97 (ревью Декса): в проде suspicious триггерится через
    active_in_db_before (файл истории на раннере не работает) — отчёт должен
    показывать именно этот базлайн, а не «медиану», если он есть."""
    monkeypatch.setitem(
        daily_report._SCOPES, "rent", ("🌆 Вечерний отчёт: аренда", tmp_path / "no.db", tmp_path / "none.json")
    )
    summary = _summary(
        tmp_path / "stats.json",
        found_in_search=3000,
        suspicious=True,
        active_in_db_before=40000,
        parse_rate_median_7=None,
    )

    text = build_daily_report("rent", summary)

    assert "Parse-rate просел" in text
    assert "3000" in text
    assert "40000" in text
    assert "активных в БД до прохода" in text


def test_missing_summary_mentioned(monkeypatch, tmp_path):
    monkeypatch.setitem(daily_report._SCOPES, "rent", ("🌆 Вечерний отчёт: аренда", tmp_path / "no.db", tmp_path / "none.json"))
    text = build_daily_report("rent")
    assert "счётчики не найдены" in text


def test_send_requires_chat_id(monkeypatch, tmp_path):
    monkeypatch.delenv("TG_ADMIN_CHAT_ID", raising=False)
    monkeypatch.setitem(daily_report._SCOPES, "rent", ("🌆 Вечерний отчёт: аренда", tmp_path / "no.db", tmp_path / "none.json"))
    assert send_daily_report("rent") is False


def test_send_calls_tg(monkeypatch, tmp_path):
    monkeypatch.setenv("TG_ADMIN_CHAT_ID", "42")
    monkeypatch.setitem(daily_report._SCOPES, "rent", ("🌆 Вечерний отчёт: аренда", tmp_path / "no.db", tmp_path / "none.json"))
    calls = []

    def fake_tg_call(method, **payload):
        calls.append((method, payload))
        return {"ok": True}

    import krisha.bot as bot

    monkeypatch.setattr(bot, "tg_call", fake_tg_call)
    assert send_daily_report("rent") is True
    assert calls[0][0] == "sendMessage"
    assert calls[0][1]["chat_id"] == "42"


# --- health_check.py (stdlib-скрипт, грузим по пути) ---

_HC_PATH = Path(__file__).resolve().parents[1] / "scripts" / "health_check.py"
_spec = importlib.util.spec_from_file_location("health_check", _HC_PATH)
health_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(health_check)


def test_health_alert_only_on_change(monkeypatch, tmp_path, capsys):
    state = tmp_path / "state.json"
    sent = []
    monkeypatch.setattr(health_check, "probe", lambda url: ("up", ""))
    monkeypatch.setattr(health_check, "send_telegram", lambda text: sent.append(text) or True)
    monkeypatch.setattr("sys.argv", ["health_check.py", "--state-file", str(state)])

    health_check.main()  # первый запуск, up → без алерта
    assert sent == []
    health_check.main()  # up → up, без алерта
    assert sent == []

    monkeypatch.setattr(health_check, "probe", lambda url: ("down", "timeout"))
    health_check.main()  # up → down: алерт
    assert len(sent) == 1
    assert "не отвечает" in sent[0]

    monkeypatch.setattr(health_check, "probe", lambda url: ("up", ""))
    health_check.main()  # down → up: алерт о восстановлении
    assert len(sent) == 2
    assert json.loads(state.read_text())["status"] == "up"


def test_health_first_run_down_alerts(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    sent = []
    monkeypatch.setattr(health_check, "probe", lambda url: ("degraded", "tg_webhook='fail'"))
    monkeypatch.setattr(health_check, "send_telegram", lambda text: sent.append(text) or True)
    monkeypatch.setattr("sys.argv", ["health_check.py", "--state-file", str(state)])

    health_check.main()  # нет прошлого состояния, но уже плохо → алерт сразу
    assert len(sent) == 1
    assert "есть проблемы" in sent[0]


def test_report_shows_mode_and_trim(monkeypatch, tmp_path):
    """issue #152: режим (выбран по backlog'у, не по флагу) и заранее
    урезанный потолок читаются из отчёта, а не восстанавливаются по воркфлоу."""
    db = tmp_path / "krisha.db"
    _make_db(db)
    monkeypatch.setitem(daily_report._SCOPES, "sale", ("🌅 Утренний отчёт: продажа", db, tmp_path / "none.json"))
    summary = _summary(
        tmp_path / "stats.json",
        mode="drain",
        mode_reason="backlog 31743 ≥ порога разгона 5000",
        delay_range=[1.5, 3.0],
        max_new_details=4140,
        plan_trimmed={"wanted_new": 4500, "wanted_refresh": 800, "reason": "time"},
    )

    text = build_daily_report("sale", summary)

    assert "Режим: <b>drain</b>" in text
    assert "backlog 31743" in text
    assert "урезан заранее с 4500+800 (time)" in text


def test_report_shows_drain_completed_and_unattributed(monkeypatch, tmp_path):
    """Переход drain → steady — событие «backlog разобран» с напоминанием о
    пересчёте дедупликации; backlog без атрибуции виден всегда при ненуле."""
    db = tmp_path / "krisha.db"
    _make_db(db)
    monkeypatch.setitem(daily_report._SCOPES, "sale", ("🌅 Утренний отчёт: продажа", db, tmp_path / "none.json"))
    summary = _summary(
        tmp_path / "stats.json",
        mode="steady",
        mode_reason="backlog 1200 < порога выхода 3000",
        delay_range=[2.0, 4.0],
        max_new_details=1200,
        drain_completed=True,
        detail_queue_after=1200,
        unattributed_backlog=45,
    )

    text = build_daily_report("sale", summary)

    assert "Backlog разобран" in text
    assert "dedup_stats.py" in text
    assert "без атрибуции к шарду: 45" in text


def _assert_telegram_html_safe(text: str) -> None:
    """Аудит 24.10: Telegram (parse_mode=HTML) отвечает 400 на голый `<`/`&`.
    Разрешены только наши теги <b>/</b> и сущности &lt; &gt; &amp;."""
    import re

    stripped = re.sub(r"</?b>", "", text)
    assert "<" not in stripped, stripped
    assert ">" not in stripped, stripped
    assert not re.search(r"&(?!(?:lt|gt|amp|quot);)", stripped), stripped


def test_report_escapes_html_from_stats(monkeypatch, tmp_path):
    """mode_reason «backlog N < порога выхода M», причина подрезки, фаза бана
    и имена шардов приходят из summary-json как есть — в тексте они должны
    быть экранированы, иначе отчёт не доходит вовсе."""
    db = tmp_path / "krisha.db"
    _make_db(db)
    monkeypatch.setitem(daily_report._SCOPES, "sale", ("🌅 Утренний отчёт: продажа", db, tmp_path / "none.json"))
    summary = _summary(
        tmp_path / "stats.json",
        mode="steady<x>",
        mode_reason="backlog 1200 < порога выхода 3000 & ещё",
        delay_range=["<2", "4&"],
        max_new_details=1200,
        plan_trimmed={"wanted_new": 4500, "wanted_refresh": 800, "reason": "time<budget & queue>"},
        banned=True,
        banned_phase="detail<&>",
        failed_shards=["almaty<1>&2"],
        starved_shards=["bostandyk&<3>"],
        detail_queue_after=1200,
        unattributed_backlog=45,
        recovery_pass=True,
        observation_gap_days="3<4",
        suspicious=True,
        active_in_db_before="9 & 9",
    )

    text = build_daily_report("sale", summary)

    _assert_telegram_html_safe(text)
    assert "backlog 1200 &lt; порога выхода 3000 &amp; ещё" in text
    assert "time&lt;budget &amp; queue&gt;" in text
    assert "detail&lt;&amp;&gt;" in text
    assert "almaty&lt;1&gt;&amp;2" in text
    assert "<b>" in text  # свои теги не экранируются


def test_report_steady_mode_reason_is_html_safe(monkeypatch, tmp_path):
    """Обычный steady-режим: реальная строка mode_reason из pass_plan."""
    db = tmp_path / "krisha.db"
    _make_db(db)
    monkeypatch.setitem(daily_report._SCOPES, "sale", ("🌅 Утренний отчёт: продажа", db, tmp_path / "none.json"))
    summary = _summary(
        tmp_path / "stats.json",
        mode="steady",
        mode_reason="backlog 1200 < порога выхода 3000",
        delay_range=[2.0, 4.0],
        max_new_details=1200,
    )
    _assert_telegram_html_safe(build_daily_report("sale", summary))
