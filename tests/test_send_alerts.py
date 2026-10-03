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
