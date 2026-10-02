"""scripts/send_alerts.py --deal arenda: после вечернего обхода — только слежка за арендой."""

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("send_alerts", ROOT / "scripts" / "send_alerts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_quiet_hours_by_almaty_time():
    sa = _load()
    # UTC+5: 18:00 UTC = 23:00 Алматы (тихо), 05:00 UTC = 10:00 Алматы (со звуком)
    assert sa._quiet_hours(datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc))
    assert sa._quiet_hours(datetime(2026, 10, 2, 1, 30, tzinfo=timezone.utc))
    assert not sa._quiet_hours(datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc))


def test_rent_mode_runs_only_rent_tracking(monkeypatch):
    sa = _load()
    from krisha import bot
    from krisha.config import RENT_DB_PATH

    seen = []

    def fake_check(**kw):
        seen.append(kw)
        return [(7, "📉 аренда")] if not kw.get("persist") else []

    monkeypatch.setattr(sa, "check_tracked_updates", fake_check)
    monkeypatch.setattr(sa, "find_good_deals", lambda: (_ for _ in ()).throw(AssertionError("deals")))
    monkeypatch.setattr(sa, "_quiet_hours", lambda now=None: True)
    sent = []
    monkeypatch.setattr(bot, "tg_call", lambda m, **kw: sent.append(kw) or {"ok": True})
    monkeypatch.setattr(sys, "argv", ["send_alerts.py", "--deal", "arenda"])

    assert sa.main() == 0
    assert [kw["deal"] for kw in seen] == ["arenda", "arenda"]
    assert all(kw["db_path"] == RENT_DB_PATH for kw in seen)
    assert seen[1]["only_chats"] == {7}
    assert sent and sent[0]["chat_id"] == 7 and sent[0]["disable_notification"] is True
