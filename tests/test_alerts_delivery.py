"""Аудит 24.10: доставка подборок — темп, 429, журнал по чатам, мёртвые чаты."""

import pytest

from krisha import alerts


def _deal(i, **kw):
    return {"id": i, "url": f"https://krisha.kz/a/show/{i}", "title": f"Лот {i}", "price": 30_000_000,
            "fair_price": 35_000_000, "diff_pct": -14.3, **kw}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Без сети и без пауз: часы рассылки — фиктивные, sleep их двигает."""
    clock = {"t": 1000.0}
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr(alerts, "_sleep", sleep)
    monkeypatch.setattr(alerts, "_now", lambda: clock["t"])
    monkeypatch.setattr(alerts, "ALERTED_PATH", tmp_path / "alerted_ids.json")
    gone, moved = [], []
    monkeypatch.setattr("krisha.subscriptions.forget_chat", gone.append)
    monkeypatch.setattr("krisha.subscriptions.migrate_chat", lambda old, new: moved.append((old, new)))
    return {"clock": clock, "sleeps": sleeps, "gone": gone, "moved": moved}


def _tg(monkeypatch, env, answer):
    """tg_call: answer(chat_id, text) → ответ Telegram; журнал отправок со временем."""
    log = []

    def tg_call(method, **kw):
        resp = answer(kw["chat_id"], kw["text"])
        log.append((env["clock"]["t"], kw["chat_id"], kw["text"].split("\n")[0], bool(resp.get("ok"))))
        return resp

    monkeypatch.setattr("krisha.bot.tg_call", tg_call)
    return log


def test_pause_between_messages_to_one_chat_is_at_least_1_1s(monkeypatch, env):
    log = _tg(monkeypatch, env, lambda chat, text: {"ok": True})
    alerts.send_alerts({"77": {}, "88": {}}, [_deal(1), _deal(2), _deal(3)])

    by_chat: dict[int, list[float]] = {}
    for t, chat, _, _ in log:
        by_chat.setdefault(chat, []).append(t)
    assert set(by_chat) == {77, 88} and all(len(ts) == 4 for ts in by_chat.values())
    for ts in by_chat.values():
        assert min(b - a for a, b in zip(ts, ts[1:])) >= alerts.ALERT_CARD_PAUSE_S - 1e-9


def test_429_is_retried_once_after_retry_after(monkeypatch, env):
    state = {"n": 0}

    def answer(chat, text):
        state["n"] += 1
        if state["n"] == 2:  # первая карточка
            return {"ok": False, "error_code": 429, "parameters": {"retry_after": 3}}
        return {"ok": True}

    log = _tg(monkeypatch, env, answer)
    alerts.send_alerts({"77": {}}, [_deal(1)])

    assert [ok for *_, ok in log] == [True, False, True]
    assert any(abs(s - 3.1) < 1e-9 for s in env["sleeps"])
    assert alerts.load_alerted() == [1]


def test_lot_is_alerted_only_when_every_selected_chat_got_it(monkeypatch, env):
    """Раньше delivered_ids был общий: дошло до 77 — лот помечен разосланным,
    и 88, у которого отправка упала, не получал его никогда."""
    down = {"88"}
    log = _tg(monkeypatch, env, lambda chat, text: {"ok": False, "error_code": 500}
              if str(chat) in down else {"ok": True})

    assert alerts.send_alerts({"77": {}, "88": {}}, [_deal(5)]) == 1
    assert alerts.load_alerted() == []
    assert alerts.load_alerted_chats() == {"77": [5]}

    down.clear()
    log.clear()
    assert alerts.send_alerts({"77": {}, "88": {}}, [_deal(5)]) == 1
    assert {chat for _, chat, *_ in log} == {88}, "77 уже получил лот — повтор ему не нужен"
    assert alerts.load_alerted() == [5]


def test_progress_survives_a_killed_step(monkeypatch, env):
    """Шаг Actions убит таймаутом посреди рассылки: разосланное уже записано,
    назавтра получившие не видят повтора, а недополучившие — получают."""
    monkeypatch.setattr(alerts, "ALERT_SAVE_EVERY", 2)
    sent = {"n": 0}

    def answer(chat, text):
        sent["n"] += 1
        if sent["n"] > 5:  # 3 заголовка + 2 карточки — и процесс гасят
            raise KeyboardInterrupt
        return {"ok": True}

    _tg(monkeypatch, env, answer)
    subs = {"1": {}, "2": {}, "3": {}}
    with pytest.raises(KeyboardInterrupt):
        alerts.send_alerts(subs, [_deal(9)])
    journal = alerts.load_alerted_chats()
    assert journal == {"1": [9], "2": [9]} and alerts.load_alerted() == []

    log = _tg(monkeypatch, env, lambda chat, text: {"ok": True})
    alerts.send_alerts(subs, [_deal(9)])
    assert {chat for _, chat, *_ in log} == {3}
    assert alerts.load_alerted() == [9]


def test_blocked_chat_is_forgotten_and_does_not_block_the_lot(monkeypatch, env):
    _tg(monkeypatch, env, lambda chat, text: {
        "ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user",
    } if chat == 88 else {"ok": True})

    assert alerts.send_alerts({"77": {}, "88": {}}, [_deal(4)]) == 1
    assert env["gone"] == [88]
    assert alerts.load_alerted() == [4], "мёртвый чат не должен держать лот неразосланным"


def test_chat_not_found_is_gone_but_other_errors_are_not():
    assert alerts.chat_is_gone({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
    assert alerts.chat_is_gone({"ok": False, "error_code": 403, "description": "Forbidden: user is deactivated"})
    assert not alerts.chat_is_gone({"ok": False, "error_code": 400, "description": "Bad Request: can't parse"})
    assert not alerts.chat_is_gone({"ok": False, "error_code": 429})
    assert not alerts.chat_is_gone(None)


def test_migrated_group_gets_message_on_new_id(monkeypatch, env):
    log = _tg(monkeypatch, env, lambda chat, text: {
        "ok": False, "error_code": 400, "description": "Bad Request: group chat was upgraded",
        "parameters": {"migrate_to_chat_id": -100777},
    } if chat == -5 else {"ok": True})

    assert alerts.send_alerts({"-5": {}}, [_deal(6)]) == 1
    assert env["moved"] == [(-5, -100777)]
    assert [chat for _, chat, _, ok in log if ok] == [-100777, -100777]
    assert alerts.load_alerted_chats() == {"-100777": [6]}
    assert alerts.load_alerted() == [6]
