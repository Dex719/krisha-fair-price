"""Inline-кнопки бота: под оценкой, в приветствии и у слежки — и что делает каждое нажатие."""

import pytest

from krisha import bot, predict_gate

RESULT = {
    "listing_id": 1012607661, "title": "2-комнатная квартира · 49 м²", "actual_price": 25_900_000,
    "fair_price": 28_700_000, "verdict": "GOOD_DEAL", "diff_pct": -9.8, "top_factors": [],
    "analogs": [{"url": "https://krisha.kz/a/show/1", "title": "2-комн · 51 м²", "price": 28_900_000}],
}


@pytest.fixture
def calls(monkeypatch):
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: sent.append((method, kw)) or {"ok": True})
    monkeypatch.setattr("krisha.usage.record_event", lambda *a, **k: None)
    bot._bot_hits.update({name: {} for name in bot._BOT_LIMITS})
    return sent


def _press(data: str, chat_id: int = 42) -> None:
    bot.handle_update({"callback_query": {"id": "cb1", "data": data, "message": {"chat": {"id": chat_id}}}})


def test_reply_keyboard_tracks_shows_similar_and_links_to_site():
    kb = bot.reply_keyboard(RESULT)["inline_keyboard"]
    assert kb[0][0] == {"text": "👀 Следить за ценой", "callback_data": "track:1012607661"}
    assert kb[0][1] == {"text": "🏘 Похожие · 1", "callback_data": "sim:1012607661"}
    assert kb[1][0]["url"] == "https://bagam.info/#check=1012607661"
    # без похожих — кнопки «Похожие» нет; без номера объявления — клавиатуры нет
    assert len(bot.reply_keyboard(dict(RESULT, analogs=[]))["inline_keyboard"][0]) == 1
    assert bot.reply_keyboard(dict(RESULT, listing_id=None, url=None)) is None


def test_track_button_runs_track_command(calls, monkeypatch):
    seen = []
    monkeypatch.setattr(bot, "_handle_track_command", lambda chat_id, text: seen.append((chat_id, text)))
    _press("track:1012607661")
    assert calls[0] == ("answerCallbackQuery", {"callback_query_id": "cb1"})
    assert seen == [(42, "/track https://krisha.kz/a/show/1012607661")]
    _press("untrack:1012607661")
    assert seen[-1] == (42, "/untrack https://krisha.kz/a/show/1012607661")


def test_similar_button_sends_analogs(calls, monkeypatch):
    monkeypatch.setattr(predict_gate, "cached_predict", lambda url, live_vision=True: RESULT)
    _press("sim:1012607661")
    text = [kw["text"] for m, kw in calls if m == "sendMessage"][-1]
    assert "Похожие квартиры" in text and "28,9 млн ₸" in text and "krisha.kz/a/show/1" in text


def test_garbage_callback_is_ignored(calls, monkeypatch):
    monkeypatch.setattr(bot, "_handle_track_command", lambda *a: pytest.fail("не должно вызываться"))
    for data in ("track:abc", "track:" + "9" * 40, "unknown:1", ""):
        _press(data)
    assert all(m == "answerCallbackQuery" for m, _ in calls)


def test_demo_button_evaluates_random_listing(calls, monkeypatch):
    evaluated = []
    monkeypatch.setattr(bot, "_demo_url", lambda deal: "https://krisha.kz/a/show/555")
    monkeypatch.setattr(bot, "_evaluate_url", lambda chat_id, url: evaluated.append(url))
    _press("demo:prodazha")
    assert evaluated == ["https://krisha.kz/a/show/555"]
    # нет примера в базе — подсказка прислать ссылку
    monkeypatch.setattr(bot, "_demo_url", lambda deal: None)
    _press("demo:arenda")
    assert "Не вижу ссылки" in [kw["text"] for m, kw in calls if m == "sendMessage"][-1]


def test_track_confirmation_has_list_and_stop_buttons(calls, monkeypatch):
    from krisha import tracking

    monkeypatch.setattr(bot, "_track_listing_meta", lambda listing_id: (25_900_000, "2-комн", "prodazha"))
    monkeypatch.setattr(tracking, "add_tracked", lambda *a, **k: (True, None))
    bot._handle_track_command(42, "/track https://krisha.kz/a/show/1012607661")
    kw = [kw for m, kw in calls if m == "sendMessage"][-1]
    assert kw["text"].startswith("👀 <b>Слежу за ценой</b>")
    assert kw["reply_markup"]["inline_keyboard"][0][1]["callback_data"] == "untrack:1012607661"


def test_webhook_subscribes_to_button_presses():
    import inspect

    assert '"callback_query"' in inspect.getsource(bot)


def test_long_reply_keeps_pre_block_whole():
    text = "строка\n<pre>Цена    1\nОценка  2</pre>\n" + "x" * 50
    clipped = bot._clip_html(text, 40)
    assert clipped.count("<pre>") == clipped.count("</pre>")


def test_alerts_go_as_header_and_cards_with_buttons(monkeypatch):
    from krisha import alerts

    sent: list[dict] = []
    monkeypatch.setattr("krisha.bot.tg_call", lambda method, **kw: sent.append(kw) or {"ok": True})
    monkeypatch.setattr(alerts, "ALERT_CARD_PAUSE_S", 0)
    monkeypatch.setattr(alerts, "remember_alerted", lambda ids: None)
    deals = [{"id": 11, "url": "https://krisha.kz/a/show/11", "title": "2-комн", "price": 38_400_000,
              "fair_price": 41_200_000, "diff_pct": -6.8, "district": "Bostandykskiy_r-n"},
             {"id": 12, "url": "https://krisha.kz/a/show/12", "title": "2-комн", "price": 44_000_000,
              "fair_price": 47_900_000, "diff_pct": -8.1}]
    assert alerts.send_alerts({"77": {}}, deals) == 1
    assert [kw["text"].split("\n")[0] for kw in sent] == [
        "🔥 <b>2 новых выгодных объявления</b>",
        "🟢 <b>−6,8% к оценке</b> · Бостандыкский",
        "🟢 <b>−8,1% к оценке</b>",
    ]
    assert sent[0]["reply_markup"]["inline_keyboard"][0][1]["callback_data"] == "alerts:off"
    card_kb = sent[1]["reply_markup"]["inline_keyboard"][0]
    assert card_kb[0]["url"] == "https://krisha.kz/a/show/11" and card_kb[1]["callback_data"] == "track:11"


def test_unsubscribe_button_runs_alerts_off(calls, monkeypatch):
    seen = []
    monkeypatch.setattr(bot, "_handle_alerts_command", lambda chat_id, text: seen.append(text))
    _press("alerts:off")
    assert seen == ["/alerts_off"]
