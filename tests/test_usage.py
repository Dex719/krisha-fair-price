"""Тесты статистики использования: запись событий и еженедельный отчёт."""

from datetime import datetime, timedelta

from krisha import usage
from krisha.usage import ALMATY_TZ, _record, weekly_report


def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "USAGE_PATH", tmp_path / "usage_stats.json")
    monkeypatch.setattr(usage, "_state", None)
    monkeypatch.setattr(usage, "_last_flush", None)
    # не коммитим в GitHub из тестов
    flushed = []
    monkeypatch.setattr(usage, "_flush", lambda state: flushed.append(state))
    return flushed


def test_record_counts_and_uniques(monkeypatch, tmp_path):
    flushed = _reset(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, 14, 30, tzinfo=ALMATY_TZ)
    _record("site", None, now)
    _record("predict", None, now)
    _record("bot", 111, now)
    _record("bot", 111, now + timedelta(minutes=1))
    _record("bot", 222, now + timedelta(minutes=2))

    day = usage._state["days"]["2026-07-03"]
    assert day["site"] == 1
    assert day["predict"] == 1
    assert day["bot"] == 3
    assert len(day["bot_users"]) == 2  # 111 задвоился — считаем уникальных
    assert day["hours"]["14"] == 5
    assert flushed  # первый вызов сразу флашится


def test_flush_throttled(monkeypatch, tmp_path):
    flushed = _reset(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, 10, 0, tzinfo=ALMATY_TZ)
    _record("site", None, now)
    _record("site", None, now + timedelta(minutes=5))
    assert len(flushed) == 1  # второй — раньше интервала
    _record("site", None, now + timedelta(minutes=31))
    assert len(flushed) == 2


def test_prune_old_days(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, 10, 0, tzinfo=ALMATY_TZ)
    usage._state = {"days": {"2026-01-01": {"site": 1}}}
    monkeypatch.setattr(usage, "_last_flush", None)
    _record("site", None, now)
    assert "2026-01-01" not in usage._state["days"]


def test_weekly_report(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, 10, 0, tzinfo=ALMATY_TZ)  # пятница
    state = {
        "days": {
            "2026-07-01": {"site": 10, "predict": 3, "bot": 5,
                           "bot_users": ["aa", "bb"], "hours": {"14": 8, "9": 2}},
            "2026-07-02": {"site": 4, "predict": 1, "bot": 2,
                           "bot_users": ["aa"], "hours": {"14": 3}},
            # старше недели — не попадает
            "2026-06-20": {"site": 100, "predict": 0, "bot": 0,
                           "bot_users": [], "hours": {"1": 100}},
        }
    }
    text = weekly_report(state, now)
    assert "визитов сайта: <b>14</b>" in text
    assert "оценок квартир: <b>4</b>" in text
    assert "уникальных пользователей бота: <b>2</b>" in text
    assert "14:00 (11)" in text
    assert "ср 2026-07-01" in text
    assert "100" not in text


def test_weekly_report_empty(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    assert weekly_report({"days": {}}, datetime.now(ALMATY_TZ)) is None


def test_record_event_never_raises(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    usage.record_event("unknown-kind")  # не бросает
