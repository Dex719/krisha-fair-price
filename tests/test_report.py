"""Тесты ежемесячного отчёта о рынке."""

from krisha import report

STATS = {
    "total_listings": 43000,
    "median_price": 45_000_000,
    "median_ppsm": 750_000,
    "trend": [{"median_ppsm": v} for v in (700_000, 710_000, 720_000, 730_000, 735_000)],
    "by_district": [{"district": "Медеуский", "median_ppsm": 950_000, "n": 5000},
                    {"district": "Алатауский", "median_ppsm": 550_000, "n": 7000}],
    "by_rooms": [{"rooms": 1, "median_price": 30_000_000},
                 {"rooms": 2, "median_price": 45_000_000}],
}


def test_build_monthly_report():
    text = report.build_monthly_report(stats=STATS)
    assert "Рынок квартир Алматы" in text
    assert "43 000" in text and "45.0 млн ₸" in text and "750 тыс ₸" in text
    assert "📈 <b>+5.0%</b>" in text  # 735/700 - 1
    assert "Медеуский: 950 тыс ₸ (5000 лотов)" in text
    assert "2-комн: 45.0 млн ₸" in text


def test_month_delta_needs_history():
    assert report._month_delta_pct([{"median_ppsm": 1}] * 4) is None
    assert report._month_delta_pct([]) is None


def test_send_monthly_report(monkeypatch):
    monkeypatch.setattr(report, "build_monthly_report", lambda: "отчёт")
    sent = []
    monkeypatch.setattr("krisha.bot.tg_call",
                        lambda m, **kw: sent.append(kw) or {"ok": True})
    monkeypatch.delenv("TG_ADMIN_CHAT_ID", raising=False)
    monkeypatch.delenv("TG_CHANNEL_ID", raising=False)
    assert report.send_monthly_report() == 0

    monkeypatch.setenv("TG_ADMIN_CHAT_ID", "42")
    monkeypatch.setenv("TG_CHANNEL_ID", "@channel")
    assert report.send_monthly_report() == 2
    assert {s["chat_id"] for s in sent} == {"42", "@channel"}
