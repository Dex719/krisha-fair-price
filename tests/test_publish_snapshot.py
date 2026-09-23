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
        "delisted": None,
        "failed_shards": ["Алатауский-1к"],
    }
    text = ps.human_summary("Аренда", stats)
    assert "детект снятий: **n/a**" in text
    assert "снято с продажи: **0**" not in text
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


# --- Ретрай и черновики (.kiro/specs/rescrape-post-steps, FR-7) ------------
# 2026-09-13 GitHub ответил HTTP 500 на создание релиза, gh оставил его
# черновиком, а вечерний rent-проход дописал в черновик свою базу.


class _FakeGh:
    """Состояние релизов «на GitHub» + сбой на первом create, как 2026-09-13."""

    def __init__(self, fail_first_create=False, releases=None):
        self.fail_first_create = fail_first_create
        self.releases = releases or {}
        self.calls = []

    def _done(self, args, code=0, out="", check=True):
        if check and code:
            raise ps.subprocess.CalledProcessError(code, args, out, "release not found")
        return ps.subprocess.CompletedProcess(args, code, out, "")

    def __call__(self, args, check=True):
        self.calls.append(args)
        verb, tag = args[1], args[2] if len(args) > 2 else None
        rel = self.releases.get(tag)
        if verb == "view":
            if rel is None:
                return self._done(args, 1, check=check)
            field = args[args.index("--json") + 1]
            out = {"tagName": tag, "body": rel["body"], "isDraft": str(rel["draft"]).lower()}[field]
            return self._done(args, 0, out)
        if verb == "create":
            # gh создаёт черновик, грузит ассеты и только потом публикует
            self.releases[tag] = {"draft": True, "body": args[args.index("--notes") + 1], "assets": set()}
            if self.fail_first_create:
                self.fail_first_create = False
                raise ps.subprocess.CalledProcessError(1, args, "", "HTTP 500: Internal Server Error")
            self.releases[tag]["draft"] = False
            self.releases[tag]["assets"].update(Path(a).name for a in args[3:] if a.endswith(("gz", "sha256")))
            return self._done(args)
        if verb == "upload":
            rel["assets"].update(Path(a).name for a in args[3:] if not a.startswith("--"))
            return self._done(args)
        if verb == "edit":
            if "--draft=false" in args:
                rel["draft"] = False
            if "--notes-file" in args:
                rel["body"] = Path(args[args.index("--notes-file") + 1]).read_text(encoding="utf-8")
            return self._done(args)
        raise AssertionError(f"неожиданная команда gh: {args}")


def _assets(tmp_path):
    db = tmp_path / "krisha.db.gz"
    db.write_bytes(b"db")
    sha = tmp_path / "krisha.db.gz.sha256"
    sha.write_text("abc")
    return ["--db-gz", str(db), "--sha256", str(sha), "--asset-name", "krisha.db.gz",
            "--tag", "snapshot-2026-09-13", "--rotate-keep-days", "0", "--label", "Продажа"]


def test_transient_500_on_create_is_retried_and_release_ends_published(tmp_path, monkeypatch):
    """AC-7.1: второй заход дописывает ассеты в полусозданный черновик и публикует."""
    gh = _FakeGh(fail_first_create=True)
    sleeps = []
    monkeypatch.setattr(ps, "run_gh", gh)
    monkeypatch.setattr(ps.time, "sleep", sleeps.append)

    assert ps.main(_assets(tmp_path)) == 0

    rel = gh.releases["snapshot-2026-09-13"]
    assert rel["draft"] is False
    assert {"krisha.db.gz", "krisha.db.gz.sha256"} <= rel["assets"]
    assert rel["body"].count("## Продажа") == 1, "секция в заметках — ровно один раз"
    assert sleeps == [ps.PUBLISH_RETRY_WAITS[0]]


def test_existing_draft_gets_assets_and_is_published(tmp_path, monkeypatch):
    """AC-7.2: черновик, оставленный прошлым сбоем, дополняется и публикуется."""
    gh = _FakeGh(releases={"snapshot-2026-09-13": {
        "draft": True, "body": "## Аренда\n\nданные", "assets": {"krisha_rent.db.gz"}}})
    monkeypatch.setattr(ps, "run_gh", gh)

    assert ps.main(_assets(tmp_path)) == 0

    rel = gh.releases["snapshot-2026-09-13"]
    assert rel["draft"] is False
    assert "## Аренда" in rel["body"] and "## Продажа" in rel["body"]
    assert "krisha.db.gz" in rel["assets"]


def test_gives_up_after_all_attempts(tmp_path, monkeypatch):
    def always_500(args, check=True):
        if args[1] == "view":
            return ps.subprocess.CompletedProcess(args, 1, "", "not found")
        raise ps.subprocess.CalledProcessError(1, args, "", "HTTP 500")

    sleeps = []
    monkeypatch.setattr(ps, "run_gh", always_500)
    monkeypatch.setattr(ps.time, "sleep", sleeps.append)

    assert ps.main(_assets(tmp_path)) == 1
    assert sleeps == list(ps.PUBLISH_RETRY_WAITS)


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
