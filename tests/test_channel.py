"""Тесты дайджеста в публичный канал."""

from krisha import channel

DEALS = [
    {"id": i, "url": f"https://krisha.kz/a/show/{i}", "title": f"Лот {i}",
     "price": 40_000_000 + i, "fair_price": 50_000_000, "diff_pct": -20.0 - i,
     "district": "Bostandykskiy_r-n", "rooms": 2, "area": 55.0}
    for i in range(1, 8)
]


def _no_persist(monkeypatch, tmp_path, posted=None):
    path = tmp_path / "posted.json"
    if posted is not None:
        import json
        path.write_text(json.dumps(posted))
    monkeypatch.setattr(channel, "POSTED_PATH", path)
    return path


def test_format_digest():
    text = channel.format_digest(DEALS[:2])
    assert "Топ-2 выгодных лотов дня" in text
    assert "Лот 1" in text and "-21.0%" in text and "Бостандыкский" in text


def test_post_digest_skips_posted_and_persists(monkeypatch, tmp_path):
    _no_persist(monkeypatch, tmp_path, posted=[1, 2])
    sent = {}
    monkeypatch.setattr("krisha.bot.tg_call",
                        lambda m, **kw: sent.update(kw) or {"ok": True})
    saved = {}
    monkeypatch.setattr(channel, "save_json_state",
                        lambda path, data, msg: saved.update(data=data))
    monkeypatch.setenv("TG_CHANNEL_ID", "@test_channel")

    text = channel.post_channel_digest(DEALS)
    assert text and "Лот 3" in text and "Лот 1" not in text  # 1-2 уже постились
    assert "Лот 7" in text and len([ln for ln in text.splitlines() if "krisha.kz" in ln]) == 5
    assert sent["chat_id"] == "@test_channel"
    assert saved["data"] == [1, 2, 3, 4, 5, 6, 7]  # добавились свежие


def test_post_digest_no_channel_or_no_fresh(monkeypatch, tmp_path):
    _no_persist(monkeypatch, tmp_path, posted=[d["id"] for d in DEALS])
    monkeypatch.setenv("TG_CHANNEL_ID", "@test_channel")
    assert channel.post_channel_digest(DEALS) is None  # всё уже постилось

    monkeypatch.delenv("TG_CHANNEL_ID")
    assert channel.post_channel_digest(DEALS[:1]) is None  # канала нет

    # dry-run работает и без канала, ничего не шлёт
    monkeypatch.setattr(channel, "POSTED_PATH", tmp_path / "fresh.json")
    text = channel.post_channel_digest(DEALS[:1], dry_run=True)
    assert text and "Лот 1" in text
