"""scripts/send_alerts.py: окно не оценилось — громко, но слежка и отчёты уходят."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("send_alerts", ROOT / "scripts" / "send_alerts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pricing_failure_alerts_admin_and_keeps_the_rest(monkeypatch):
    sa = _load()
    called = []

    def broken():
        raise sa.AlertsPricingFailed("ни один из 5 лотов окна не оценился")

    monkeypatch.setattr(sa, "find_good_deals", broken)
    for name in ("_send_deal_alerts", "_post_channel_digest"):
        monkeypatch.setattr(sa, name, lambda *a, _n=name: called.append(_n))
    for name in ("_send_track_alerts", "_maybe_monthly_report",
                 "_maybe_weekly_usage_report", "_send_daily_admin_report"):
        monkeypatch.setattr(sa, name, lambda *a, _n=name, **k: called.append(_n))
    admin = []
    monkeypatch.setattr(sa, "_alert_admin", admin.append)
    monkeypatch.setattr(sys, "argv", ["send_alerts.py"])

    assert sa.main() == 1
    assert "_send_deal_alerts" not in called and "_post_channel_digest" not in called
    assert {"_send_track_alerts", "_send_daily_admin_report"} <= set(called)
    assert admin and "не оценился" in admin[0]


# --- chat_id в публичном логе Actions -------------------------------------------

CHAT = 987654321


def test_mask_chat_id_keeps_only_two_last_digits():
    from krisha.bot import mask_chat_id

    assert mask_chat_id(CHAT) == "***21"
    assert mask_chat_id("-1001234567890") == "***90"
    assert mask_chat_id(None) == "***"


def test_track_alerts_never_print_full_chat_id(monkeypatch, capsys):
    """Лог рассылки публичный (GitHub Actions открытого репо): ни dry-run, ни
    «не доставлено» не печатают номер чата подписчика целиком."""
    sa = _load()
    from krisha import bot

    monkeypatch.setattr(
        sa, "check_tracked_updates", lambda **kw: [] if kw.get("persist") else [(CHAT, "📉 цена")]
    )
    monkeypatch.setattr(sa, "_quiet_hours", lambda now=None: False)
    monkeypatch.setattr(bot, "tg_call", lambda m, **kw: {"ok": False, "error_code": 403})

    sa._send_track_alerts(dry_run=True)
    sa._send_track_alerts(dry_run=False)

    out = capsys.readouterr().out
    assert "Не доставлено в chat ***21" in out and "--- chat ***21" in out
    assert str(CHAT) not in out


def test_deal_alerts_dry_run_never_prints_full_chat_id(monkeypatch, capsys):
    sa = _load()
    monkeypatch.setattr(sa, "load_subscriptions", lambda: {str(CHAT): {}})
    monkeypatch.setattr(sa, "match_filters", lambda deal, flt: False)

    sa._send_deal_alerts(True, [{"id": 1}])

    out = capsys.readouterr().out
    assert "--- chat ***21: 0 лотов" in out
    assert str(CHAT) not in out
