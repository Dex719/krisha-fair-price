"""Лимиты бота на чат: скользящее окно, три ведра, один ответ «подожди» за окно.

Аудит безопасности 2026-10-06: вебхук защищён секретом, но писать боту может
любой пользователь Telegram — и каждое сообщение стоило нам чужого ресурса
(поход на krisha.kz, коммит в krisha-db, живой вызов Gemini).
"""

import threading

import pytest

from krisha import bot

BUCKET_ENV = ("BOT_RATE_LIMIT_PER_MIN", "BOT_TEXT_LIMIT_PER_MIN", "BOT_STATE_LIMIT_PER_MIN")
LONG_TEXT = "Продам уютную двухкомнатную квартиру в центре города, торг уместен"  # >= 40 символов
assert len(LONG_TEXT) >= 40


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch):
    """Состояние лимитера живёт в модуле — чистим до и после каждого теста."""
    for name in BUCKET_ENV:
        monkeypatch.delenv(name, raising=False)
    bot.reset_rate_limits()
    yield
    bot.reset_rate_limits()


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(bot, "_rate_now", lambda: now[0])
    return now


@pytest.fixture
def sent(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True}
    )
    return calls


@pytest.fixture
def events(monkeypatch):
    """Счётчик record_event: отброшенные сообщения в статистику идти не должны."""
    seen: list[str] = []
    monkeypatch.setattr(
        "krisha.usage.record_event", lambda kind, **kw: seen.append(kind)
    )
    return seen


@pytest.fixture
def handlers(monkeypatch):
    """Тяжёлые обработчики заменены счётчиками: проверяем только лимитер."""
    calls = {"alerts": [], "track": [], "gemini": []}
    monkeypatch.setattr(bot, "_handle_alerts_command", lambda cid, text: calls["alerts"].append(text))
    monkeypatch.setattr(bot, "_handle_track_command", lambda cid, text: calls["track"].append(text))
    monkeypatch.setattr(
        "krisha.text_parse.predict_from_text", lambda text: calls["gemini"].append(text)
    )
    return calls


def _msg(chat_id, text):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def _notices(sent):
    return [kw for m, kw in sent if m == "sendMessage" and kw.get("text") == bot.RATE_LIMITED_TEXT]


# --- общий поток ----------------------------------------------------------


def test_general_bucket_blocks_after_limit_and_notifies_once(sent, events, clock):
    for _ in range(12):
        bot.handle_update(_msg(42, "/help"))
    assert len(sent) == 12 and not _notices(sent)

    bot.handle_update(_msg(42, "/help"))  # 13-е
    assert len(_notices(sent)) == 1
    assert _notices(sent)[0]["chat_id"] == 42

    for _ in range(20):  # спам дальше — молча
        bot.handle_update(_msg(42, "/help"))
    assert len(sent) == 13 and len(_notices(sent)) == 1
    # отброшенные в статистику бота не попадают
    assert len(events) == 12


def test_window_slides_and_notice_is_once_per_window(sent, clock):
    for _ in range(12):
        bot.handle_update(_msg(42, "/help"))  # t=1000
    clock[0] = 1030
    bot.handle_update(_msg(42, "/help"))  # заблокирован → «подожди»
    assert len(sent) == 13 and len(_notices(sent)) == 1

    clock[0] = 1060  # самые старые сообщения ровно 60 с назад — ещё в окне
    bot.handle_update(_msg(42, "/help"))
    assert len(sent) == 13

    clock[0] = 1061  # окно из первых 12 сообщений истекло
    bot.handle_update(_msg(42, "/help"))
    assert len(sent) == 14 and len(_notices(sent)) == 1

    # снова упёрлись (в окне 12 сообщений); предупреждение было 31 с назад —
    # это то же окно, молчим
    for _ in range(11):
        bot.handle_update(_msg(42, "/help"))
    assert len(sent) == 25
    for _ in range(3):
        bot.handle_update(_msg(42, "/help"))
    assert len(sent) == 25 and len(_notices(sent)) == 1

    # с прошлого предупреждения прошло больше окна — можно предупредить снова
    clock[0] = 1091
    bot.handle_update(_msg(42, "/help"))
    assert len(sent) == 26 and len(_notices(sent)) == 2


def test_notice_is_not_sent_for_every_dropped_message(sent, clock):
    for _ in range(12):
        bot.handle_update(_msg(42, "/help"))
    for _ in range(50):
        bot.handle_update(_msg(42, "/help"))
    assert len(_notices(sent)) == 1


def test_chats_do_not_interfere(sent, clock):
    for _ in range(13):
        bot.handle_update(_msg(1, "/help"))
    assert len(_notices(sent)) == 1

    before = len(sent)
    for _ in range(12):
        bot.handle_update(_msg(2, "/help"))
    assert len(sent) == before + 12  # чужой чат лимит первого не делит
    assert len(_notices(sent)) == 1


def test_edited_message_counts_too(sent, clock):
    for _ in range(12):
        bot.handle_update({"edited_message": {"chat": {"id": 5}, "text": "/help"}})
    bot.handle_update(_msg(5, "/help"))
    assert len(_notices(sent)) == 1


def test_link_messages_use_only_the_general_bucket(sent, handlers, monkeypatch, clock):
    from krisha import predict_gate

    calls = []
    monkeypatch.setattr(
        predict_gate, "cached_predict", lambda url, live_vision=True: calls.append(url) or {
            "fair_price": 1.0}
    )
    for i in range(12):
        bot.handle_update(_msg(7, f"https://krisha.kz/a/show/{100 + i}"))
    assert len(calls) == 12
    bot.handle_update(_msg(7, "https://krisha.kz/a/show/999"))
    assert len(calls) == 12 and len(_notices(sent)) == 1


# --- свободный текст (Gemini) ----------------------------------------------


def test_free_text_bucket_limits_gemini_calls(sent, handlers, clock):
    for i in range(3):
        bot.handle_update(_msg(42, f"{LONG_TEXT} #{i}"))
    assert len(handlers["gemini"]) == 3

    bot.handle_update(_msg(42, f"{LONG_TEXT} #4"))
    assert len(handlers["gemini"]) == 3  # до Gemini не дошло
    assert len(_notices(sent)) == 1

    bot.handle_update(_msg(42, f"{LONG_TEXT} #5"))
    assert len(handlers["gemini"]) == 3 and len(_notices(sent)) == 1

    clock[0] += 61
    bot.handle_update(_msg(42, f"{LONG_TEXT} #6"))
    assert len(handlers["gemini"]) == 4


def test_short_text_and_links_do_not_spend_the_gemini_bucket(sent, handlers, monkeypatch, clock):
    from krisha import predict_gate

    monkeypatch.setattr(predict_gate, "cached_predict", lambda url, live_vision=True: {"fair_price": 1.0})
    for _ in range(4):
        bot.handle_update(_msg(42, "привет"))  # < 40 символов — подсказка без Gemini
    bot.handle_update(_msg(42, "https://krisha.kz/a/show/1 " + LONG_TEXT))  # есть ссылка
    for i in range(3):
        bot.handle_update(_msg(42, f"{LONG_TEXT} #{i}"))
    assert len(handlers["gemini"]) == 3
    assert not _notices(sent)


def test_text_limit_has_its_own_bucket(sent, handlers, clock):
    for i in range(4):
        bot.handle_update(_msg(42, f"{LONG_TEXT} #{i}"))  # 4-е упёрлось в text
    assert len(_notices(sent)) == 1

    before = len(sent)
    bot.handle_update(_msg(42, "/help"))  # общий поток и другие ведра живы
    assert len(sent) == before + 1


def test_dropped_message_does_not_spend_the_general_bucket(sent, handlers, monkeypatch, clock):
    monkeypatch.setenv("BOT_RATE_LIMIT_PER_MIN", "4")
    monkeypatch.setenv("BOT_TEXT_LIMIT_PER_MIN", "1")
    bot.handle_update(_msg(42, LONG_TEXT))  # проходит: msg 1/4, text 1/1
    for _ in range(5):
        bot.handle_update(_msg(42, LONG_TEXT))  # text заполнено → отброшены целиком
    sent.clear()
    for _ in range(3):
        bot.handle_update(_msg(42, "/help"))  # msg: 1 + 3 = 4 — ровно по лимиту
    assert len(sent) == 3 and not _notices(sent)
    bot.handle_update(_msg(42, "/help"))  # общий лимит исчерпан, но «подожди» в этом окне уже было
    assert len(sent) == 3


# --- команды, пишущие состояние ---------------------------------------------


@pytest.mark.parametrize("text", [
    "/alerts_on",
    "/alerts_on 2к до 45млн бостандыкский",
    "/alerts_on@bagam_bot 2к",
    "/alerts_off",
    "/track https://krisha.kz/a/show/123456789",
    "/track",
    "/track@bagam_bot https://krisha.kz/a/show/1",
    "/untrack https://krisha.kz/a/show/123456789",
    "/untrack all",
    "/start track_123456789",
    "/start market_bostandykskiy",
])
def test_state_commands_share_the_state_bucket(text, sent, handlers, monkeypatch, clock):
    monkeypatch.setenv("BOT_RATE_LIMIT_PER_MIN", "100")  # общий не мешает
    for _ in range(5):
        bot.handle_update(_msg(42, text))
    assert not _notices(sent)

    bot.handle_update(_msg(42, text))
    assert len(_notices(sent)) == 1
    handled = len(handlers["alerts"]) + len(handlers["track"])
    assert handled == 5  # шестая до хендлера не дошла


def test_state_bucket_is_shared_across_command_kinds(sent, handlers, monkeypatch, clock):
    monkeypatch.setenv("BOT_RATE_LIMIT_PER_MIN", "100")
    texts = ["/alerts_on", "/alerts_off", "/track https://krisha.kz/a/show/1",
             "/untrack all", "/start track_5"]
    for text in texts:
        bot.handle_update(_msg(42, text))
    assert not _notices(sent)
    bot.handle_update(_msg(42, "/alerts_on"))
    assert len(_notices(sent)) == 1


def test_read_only_commands_do_not_spend_the_state_bucket(sent, handlers, monkeypatch, clock):
    monkeypatch.setenv("BOT_RATE_LIMIT_PER_MIN", "100")
    for text in ["/alerts", "/alerts", "/help", "/start", "/start unknown", "/alerts@bagam_bot"] * 3:
        bot.handle_update(_msg(42, text))
    assert not _notices(sent)
    # а ведро state по-прежнему пустое
    for _ in range(5):
        bot.handle_update(_msg(42, "/alerts_on"))
    assert not _notices(sent)


def test_state_limit_is_per_chat(sent, handlers, monkeypatch, clock):
    monkeypatch.setenv("BOT_RATE_LIMIT_PER_MIN", "100")
    for _ in range(6):
        bot.handle_update(_msg(1, "/alerts_on"))
    assert len(_notices(sent)) == 1
    for _ in range(5):
        bot.handle_update(_msg(2, "/alerts_on"))
    assert len(_notices(sent)) == 1


# --- env ---------------------------------------------------------------------


def test_env_overrides_limits(sent, handlers, monkeypatch, clock):
    monkeypatch.setenv("BOT_RATE_LIMIT_PER_MIN", "2")
    bot.handle_update(_msg(1, "/help"))
    bot.handle_update(_msg(1, "/help"))
    bot.handle_update(_msg(1, "/help"))
    assert len(_notices(sent)) == 1

    monkeypatch.setenv("BOT_TEXT_LIMIT_PER_MIN", "1")
    bot.handle_update(_msg(2, LONG_TEXT))
    bot.handle_update(_msg(2, LONG_TEXT))
    assert len(handlers["gemini"]) == 1 and len(_notices(sent)) == 2

    monkeypatch.setenv("BOT_STATE_LIMIT_PER_MIN", "1")
    bot.handle_update(_msg(3, "/alerts_on"))
    bot.handle_update(_msg(3, "/alerts_on"))
    assert len(handlers["alerts"]) == 1 and len(_notices(sent)) == 3


def test_bad_env_falls_back_to_defaults(monkeypatch):
    assert [bot._bot_limit(b) for b in ("msg", "text", "state")] == [12, 3, 5]
    for bad in ("", "abc", "1.5"):
        for name in BUCKET_ENV:
            monkeypatch.setenv(name, bad)
        assert [bot._bot_limit(b) for b in ("msg", "text", "state")] == [12, 3, 5]
    for name in BUCKET_ENV:
        monkeypatch.setenv(name, "0")  # ноль не должен выключать бота целиком
    assert [bot._bot_limit(b) for b in ("msg", "text", "state")] == [1, 1, 1]


# --- память ------------------------------------------------------------------


def test_stale_keys_are_evicted_when_table_overflows(monkeypatch, clock):
    monkeypatch.setattr(bot, "BOT_MAX_RATE_KEYS", 5)
    for chat in range(6):
        bot._bot_rate_check(chat, ("msg",))
    assert len(bot._bot_hits["msg"]) == 6  # потолок пробит, чистка — на следующем вызове

    clock[0] += 61  # все записи протухли
    bot._bot_rate_check(100, ("msg",))
    assert set(bot._bot_hits["msg"]) == {"100"}


def test_live_keys_are_capped_too(monkeypatch, clock):
    monkeypatch.setattr(bot, "BOT_MAX_RATE_KEYS", 10)
    for chat in range(200):
        clock[0] += 0.01  # все живые, но с разным «возрастом»
        bot._bot_rate_check(chat, ("msg", "text"))
    for name in ("msg", "text"):
        assert len(bot._bot_hits[name]) <= 11
        assert "199" in bot._bot_hits[name] and "0" not in bot._bot_hits[name]


def test_notified_table_is_bounded(monkeypatch, clock):
    monkeypatch.setattr(bot, "BOT_MAX_RATE_KEYS", 10)
    monkeypatch.setenv("BOT_RATE_LIMIT_PER_MIN", "1")
    for chat in range(100):
        clock[0] += 0.01
        bot._bot_rate_check(chat, ("msg",))  # ok
        bot._bot_rate_check(chat, ("msg",))  # notify → запись в _bot_notified
    assert len(bot._bot_notified) <= 11


def test_limiter_is_thread_safe(monkeypatch):
    """12 «ok» на чат и ни одним больше, сколько бы потоков ни стучалось разом."""
    results: list[str] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(50):
            results.append(bot._bot_rate_check(42, ("msg",)))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 12
    assert results.count("notify") == 1
    assert results.count("drop") == 8 * 50 - 13


# --- маршрутизация вёдер ----------------------------------------------------


@pytest.mark.parametrize("text,bucket", [
    ("/start", None),
    ("/start track_123", "state"),
    ("/start market_medeuskiy", "state"),
    ("/start@bagam_bot track_1", "state"),
    ("/start whatever", None),
    ("/help", None),
    ("/alerts", None),
    ("/alerts_on", "state"),
    ("/alerts_on@bagam_bot 2к", "state"),
    ("/alerts_off", "state"),
    ("/alerts_onfoo", None),  # обработчик такую команду трактует как справку
    ("/track", "state"),
    ("/track https://krisha.kz/a/show/1", "state"),
    ("/untrack all", "state"),
    ("https://krisha.kz/a/show/123", None),
    ("глянь https://krisha.kz/a/show/123 " + LONG_TEXT, None),
    ("привет", None),
    (LONG_TEXT, "text"),
    ("/unknown " + LONG_TEXT, "text"),
])
def test_limit_bucket_routing(text, bucket):
    assert bot._limit_bucket(text) == bucket
