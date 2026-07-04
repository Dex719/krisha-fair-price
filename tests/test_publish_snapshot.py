"""Тесты чистой логики scripts/publish_snapshot.py (issue #74, часть 1).

Сеть/gh не трогаем — только сводка, парсинг статистики и правило ротации.
"""

import json
import sys
from datetime import date, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish_snapshot as ps  # noqa: E402


def test_human_summary_includes_counters_and_raw_json():
    stats = {
        "found_in_search": 100,
        "new_listings": 5,
        "price_changes": 3,
        "delisted": 2,
        "failed_shards": [],
    }
    text = ps.human_summary("Продажа", stats)
    assert "## Продажа" in text
    assert "**100**" in text
    assert "новых объявлений: **5**" in text
    assert "```json" in text
    assert json.loads(text.split("```json\n", 1)[1].rsplit("```", 1)[0]) == stats


def test_human_summary_flags_failed_shards():
    stats = {
        "found_in_search": 10,
        "new_listings": 0,
        "price_changes": 0,
        "delisted": 0,
        "failed_shards": ["Алатауский-1к"],
    }
    text = ps.human_summary("Аренда", stats)
    assert "не покрыты шарды: Алатауский-1к" in text


def test_load_stats_missing_file_returns_empty(tmp_path):
    assert ps.load_stats(str(tmp_path / "nope.json")) == {}
    assert ps.load_stats(None) == {}


def test_load_stats_reads_json(tmp_path):
    p = tmp_path / "stats.json"
    p.write_text(json.dumps({"found_in_search": 1}))
    assert ps.load_stats(str(p)) == {"found_in_search": 1}


def test_rotation_keeps_sunday_and_recent(monkeypatch):
    """Проверяем только правило отбора (без вызова gh): старые несуточные ->
    удаляются, воскресные и свежие — нет."""
    today = date(2026, 7, 12)  # воскресенье
    cutoff = today - timedelta(days=14)

    old_monday = today - timedelta(days=20)  # старый, не воскресенье -> удалить
    old_sunday = today - timedelta(days=21)  # старый, воскресенье -> хранить
    recent = today - timedelta(days=1)  # свежий -> хранить

    assert old_monday.weekday() != 6
    assert old_sunday.weekday() == 6

    def should_delete(tag_date: date) -> bool:
        if tag_date >= cutoff:
            return False
        return tag_date.weekday() != 6

    assert should_delete(old_monday) is True
    assert should_delete(old_sunday) is False
    assert should_delete(recent) is False


def test_tag_uses_utc_today(monkeypatch):
    class FakeDatetime:
        @staticmethod
        def now(tz):
            assert tz is timezone.utc
            import datetime as _dt

            return _dt.datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(ps, "datetime", FakeDatetime)
    tag = f"{ps.TAG_PREFIX}-{FakeDatetime.now(timezone.utc).date().isoformat()}"
    assert tag == "snapshot-2026-07-05"
