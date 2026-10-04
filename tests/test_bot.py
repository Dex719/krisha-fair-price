"""Тесты Telegram-бота: форматирование и обработка апдейтов (без сети)."""

from krisha import bot, predict_gate

SAMPLE_RESULT = {
    "listing_id": 123,
    "url": "https://krisha.kz/a/show/123",
    "title": "2-комнатная квартира, 60 м², 5/9 этаж",
    "address": "Алматы, Бостандыкский р-н",
    "actual_price": 52_000_000,
    "fair_price": 48_120_000.0,
    "verdict": "FAIR",
    "diff_pct": 8.1,
    "top_factors": [
        {"feature": "area", "impact": 0.21},
        {"feature": "dist_center_km", "impact": -0.08},
    ],
    "photos": ["https://example.com/p1.jpg"],
}


def test_extract_url():
    assert bot.extract_url("глянь https://krisha.kz/a/show/757565999 пожалуйста") == (
        "https://krisha.kz/a/show/757565999"
    )
    assert bot.extract_url("просто текст") is None


def test_predict_from_url_canonicalizes_against_ssrf(monkeypatch):
    """SSRF-защита: фетчим только канонический krisha-URL по id, а не сырой ввод."""
    import pytest

    from krisha import predict as predmod

    captured = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            captured["url"] = url
            return None  # None → RuntimeError, нам важен только захваченный url

    monkeypatch.setattr(predmod, "PoliteClient", FakeClient)
    # Вредоносный ввод: подстрока krisha.kz/a/show есть, но хост — внутренний.
    with pytest.raises(RuntimeError):
        predmod.predict_from_url("http://169.254.169.254/krisha.kz/a/show/777?x=1")
    assert captured["url"] == "https://krisha.kz/a/show/777"


def test_format_reply_contains_key_fields():
    text = bot.format_reply(SAMPLE_RESULT)
    assert "52 000 000 ₸" in text
    assert "48 120 000 ₸" in text
    assert "Справедливая цена" in text
    assert "+8.1%" in text
    assert "Площадь" in text and "▲" in text
    assert "Расстояние до центра" in text and "▼" in text


def test_format_reply_without_actual_price():
    result = dict(SAMPLE_RESULT, actual_price=None, verdict=None, diff_pct=None)
    text = bot.format_reply(result)
    assert "Цена в объявлении" not in text
    assert "48 120 000 ₸" in text


def test_format_reply_liquidity_band_and_segment():
    liq = {"median_days": 18, "sample": 25, "scope": "district_rooms",
           "band": "above", "band_median_days": 33, "band_sample": 21}
    text = bot.format_reply(dict(SAMPLE_RESULT, liquidity=liq))
    # есть полоса → показываем её, а не общий сегмент
    assert "Похожие по цене (дороже рынка)" in text
    assert "~<b>33 дн.</b>" in text and "по 21 снятым" in text

    liq_seg = dict(liq, band=None, band_median_days=None, band_sample=None)
    text = bot.format_reply(dict(SAMPLE_RESULT, liquidity=liq_seg))
    assert "Похожие снимают с продажи за ~<b>18 дн.</b>" in text

    text = bot.format_reply(dict(SAMPLE_RESULT, liquidity=dict(liq_seg, scope="city")))
    assert "Похожие по городу снимают" in text

    assert "снимают" not in bot.format_reply(dict(SAMPLE_RESULT, liquidity=None))


def test_handle_update_start_sends_help(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "/start"}})
    assert calls and calls[0][0] == "sendMessage"
    assert calls[0][1]["chat_id"] == 42
    assert "krisha.kz" in calls[0][1]["text"]


def test_start_track_payload_enables_tracking_without_full_help(tmp_path, monkeypatch):
    from krisha import tracking

    monkeypatch.setattr("krisha.subscriptions._push_to_github", lambda *a, **k: None)
    monkeypatch.setattr(tracking, "TRACKED_PATH", tmp_path / "tracked.json")
    monkeypatch.setattr(bot, "_track_listing_meta", lambda lid: (51_000_000, "Payload lot", "prodazha"))
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})

    bot.handle_update({"message": {"chat": {"id": 42}, "text": "/start track_123456789"}})

    texts = [kw["text"] for method, kw in calls if method == "sendMessage"]
    assert tracking.list_tracked(42) != {}
    assert any("Слежу" in text and "Payload lot" in text for text in texts)
    assert all("🏠 <b>FairPrice</b>" not in text for text in texts)


def test_start_market_payload_subscribes_to_district_without_full_help(tmp_path, monkeypatch):
    from krisha import subscriptions

    monkeypatch.setattr("krisha.subscriptions._push_to_github", lambda *a, **k: None)
    monkeypatch.setattr(subscriptions, "SUBSCRIPTIONS_PATH", tmp_path / "subscriptions.json")
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})

    bot.handle_update({"message": {"chat": {"id": 42}, "text": "/start market_bostandykskiy"}})

    sub = subscriptions.load_subscriptions()["42"]
    texts = [kw["text"] for method, kw in calls if method == "sendMessage"]
    assert sub["district"] == "Bostandykskiy_r-n"
    assert sub["rooms"] is None and sub["max_price"] is None
    assert any("Бостандыкский" in text and "/alerts_off" in text for text in texts)
    assert all("🏠 <b>FairPrice</b>" not in text for text in texts)


def test_start_market_unknown_payload_explains_manual_subscription(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})

    bot.handle_update({"message": {"chat": {"id": 42}, "text": "/start market_unknown"}})

    assert calls and calls[-1][0] == "sendMessage"
    assert "район не узнал" in calls[-1][1]["text"]
    assert "/alerts_on" in calls[-1][1]["text"]
    assert "🏠 <b>FairPrice</b>" not in calls[-1][1]["text"]


def test_handle_update_no_url_hint(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "сколько стоит квартира?"}})
    assert calls[0][0] == "sendMessage"
    assert "Не вижу ссылки" in calls[0][1]["text"]


def test_handle_update_predicts_and_sends_photo(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})
    monkeypatch.setattr(predict_gate, "predict_from_url", lambda url, live_vision=True, timeout=None, on_attempt=None: SAMPLE_RESULT)
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "https://krisha.kz/a/show/123"}})
    methods = [m for m, _ in calls]
    assert "sendChatAction" in methods
    assert "sendPhoto" in methods
    photo_call = [kw for m, kw in calls if m == "sendPhoto"][0]
    assert photo_call["photo"] == "https://example.com/p1.jpg"
    assert "48 120 000 ₸" in photo_call["caption"]


def test_handle_update_explains_source_failure_instead_of_generic_error(monkeypatch):
    """safeline-468 §7: любой отказ krisha — «источник не отдаёт, повтори»,
    а не «Не получилось оценить объявление: …» с сырым текстом исключения."""
    from krisha.scraping.client import SourceUnavailable

    def unavailable(url, live_vision=True, timeout=None, on_attempt=None):
        raise SourceUnavailable("3 попытки без страницы")

    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})
    monkeypatch.setattr(predict_gate, "predict_from_url", unavailable)
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "https://krisha.kz/a/show/123"}})

    texts = [kw.get("text", "") for m, kw in calls if m == "sendMessage"]
    assert any("krisha сейчас не отдаёт" in t for t in texts)
    assert not any("Не получилось оценить" in t for t in texts)


def test_handle_update_says_listing_was_removed(monkeypatch):
    """predict-edge-listings AC-4.1: снятое объявление — понятный ответ."""
    from krisha.predict import ListingNotFound

    def removed(url, live_vision=True, timeout=None, on_attempt=None):
        raise ListingNotFound("Объявление не найдено")

    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})
    monkeypatch.setattr(predict_gate, "predict_from_url", removed)
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "https://krisha.kz/a/show/123"}})

    texts = [kw.get("text", "") for m, kw in calls if m == "sendMessage"]
    assert any("сняли с продажи" in t for t in texts)


def test_handle_update_never_leaves_user_without_answer(monkeypatch):
    """AC-4.1: непредвиденная ошибка оценки (раньше — TypeError на объявлении без
    цены) не должна превращаться в тишину."""

    def broken(url, live_vision=True, timeout=None, on_attempt=None):
        raise TypeError("что-то непредвиденное")

    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})
    monkeypatch.setattr(predict_gate, "predict_from_url", broken)
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "https://krisha.kz/a/show/124"}})

    texts = [kw.get("text", "") for m, kw in calls if m == "sendMessage"]
    assert any("Не получилось оценить объявление" in t for t in texts)


def test_handle_update_ignores_non_message():
    # не должно падать
    bot.handle_update({"callback_query": {"id": "1"}})
    bot.handle_update({"message": {"chat": {"id": 42}}})  # без текста


def test_webhook_secret_is_stable():
    s1 = bot.webhook_secret("token123")
    assert s1 == bot.webhook_secret("token123")
    assert len(s1) == 32
    assert s1 != bot.webhook_secret("other")


def test_format_reply_factor_magnitudes():
    """«Почему такая цена»: топ-3 фактора с процентами и деньгами."""
    result = dict(
        SAMPLE_RESULT,
        top_factors=[
            {"feature": "area", "impact": 0.21, "impact_pct": 23.4, "impact_tenge": 9_100_000},
            {"feature": "dist_center_km", "impact": -0.08, "impact_pct": -7.7,
             "impact_tenge": -3_900_000},
            {"feature": "rooms", "impact": 0.02, "impact_pct": 2.0, "impact_tenge": 950_000},
            {"feature": "floor", "impact": 0.01, "impact_pct": 1.0, "impact_tenge": 480_000},
        ],
    )
    text = bot.format_reply(result)
    assert "Почему такая цена" in text
    assert "Площадь: +23.4% (+9.1 млн ₸)" in text
    assert "Расстояние до центра: -7.7% (-3.9 млн ₸)" in text
    assert "Комнаты: +2.0% (+0.9 млн ₸)" in text
    assert "Этаж" not in text  # только топ-3


def test_start_adds_miniapp_button_in_private(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://kfp.example.com")
    bot.handle_update({"message": {"chat": {"id": 42, "type": "private"}, "text": "/start"}})
    kb = calls[0][1]["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["web_app"] == {"url": "https://kfp.example.com"}

    # в группе web_app-кнопку не шлём (Telegram их там не разрешает)
    calls.clear()
    bot.handle_update({"message": {"chat": {"id": -1, "type": "group"}, "text": "/help"}})
    assert "reply_markup" not in calls[0][1]

    # без публичного URL — обычный help
    calls.clear()
    monkeypatch.delenv("PUBLIC_BASE_URL")
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    monkeypatch.delenv("SPACE_HOST", raising=False)
    bot.handle_update({"message": {"chat": {"id": 42, "type": "private"}, "text": "/start"}})
    assert "reply_markup" not in calls[0][1]


def test_webhook_status_self_heals(monkeypatch):
    """Если webhook слетел (url пустой) — health-проверка перерегистрирует его."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://kfp.example.com")
    calls = []

    def fake_tg(method, **kw):
        calls.append((method, kw))
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": ""}}  # webhook не зарегистрирован
        return {"ok": True}

    monkeypatch.setattr(bot, "tg_call", fake_tg)
    bot._last_webhook_check[0] = None

    assert bot.webhook_status(force=True) == "ok"
    set_calls = [kw for m, kw in calls if m == "setWebhook"]
    assert set_calls and set_calls[0]["url"] == "https://kfp.example.com/tg/webhook"

    # повторный вызов в течение часа не дёргает Telegram (кэш)
    calls.clear()
    assert bot.webhook_status() == "ok"
    assert calls == []


def test_webhook_stays_on_space_host_with_custom_domain(monkeypatch):
    """Свой домен (PUBLIC_BASE_URL) — для ссылок; webhook остаётся на адресе
    Space и не зависит от Cloudflare-прокси домена."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bagam.info")
    monkeypatch.setenv("SPACE_HOST", "dex719-krisha-fair-price.hf.space")
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})

    assert bot.public_base_url() == "https://bagam.info"
    assert bot.setup_webhook() is True
    assert calls[0][1]["url"] == "https://dex719-krisha-fair-price.hf.space/tg/webhook"


def test_setup_webhook_keeps_pending_updates(monkeypatch):
    """Рестарт не имеет права выбрасывать недоставленные сообщения.

    Space пересобирается каждую ночь и рестартует на каждый деплой; с
    drop_pending_updates=True всё, что Telegram не успел доставить, исчезало
    молча. Дубли закрыты дедупом (in-proc deque + таблица tg_updates).
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://kfp.example.com")
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})

    assert bot.setup_webhook() is True
    assert calls[0][0] == "setWebhook"
    assert "drop_pending_updates" not in calls[0][1]


def test_webhook_status_ok_when_url_matches(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://kfp.example.com")
    monkeypatch.setattr(
        bot,
        "tg_call",
        lambda method, **kw: {"ok": True, "result": {"url": "https://kfp.example.com/tg/webhook"}},
    )
    assert bot.webhook_status(force=True) == "ok"


def test_webhook_status_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert bot.webhook_status(force=True) == "no_token"


def test_webhook_status_failure_not_cached(monkeypatch):
    """Неудачный getWebhookInfo не кэшируется на час — следующий пинг пробует снова."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://kfp.example.com")
    calls = []

    def flaky_tg(method, **kw):
        calls.append(method)
        if len(calls) == 1:
            return None  # сеть моргнула при первом вызове
        return {"ok": True, "result": {"url": "https://kfp.example.com/tg/webhook"}}

    monkeypatch.setattr(bot, "tg_call", flaky_tg)
    bot._last_webhook_check[0] = None

    clock = [10_000.0]
    monkeypatch.setattr(bot.time, "monotonic", lambda: clock[0])
    assert bot.webhook_status() == "unknown"
    # следующий пинг сразу Telegram не дёргает (при недоступном API каждый
    # вызов висел ~30 с и держал /api/health), но и не ждёт часа
    assert bot.webhook_status() == "unknown"
    assert calls == ["getWebhookInfo"]
    clock[0] += bot._WEBHOOK_RETRY_S + 1
    assert bot.webhook_status() == "ok"
    assert calls == ["getWebhookInfo", "getWebhookInfo"]


def test_webhook_status_checks_on_fresh_boot(monkeypatch):
    """На свежем контейнере (monotonic < часа) первый health-пинг реально дёргает Telegram."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://kfp.example.com")
    monkeypatch.setattr(bot.time, "monotonic", lambda: 42.0)  # аптайм 42 секунды
    calls = []

    def fake_tg(method, **kw):
        calls.append(method)
        return {"ok": True, "result": {"url": "https://kfp.example.com/tg/webhook"}}

    monkeypatch.setattr(bot, "tg_call", fake_tg)
    bot._last_webhook_check[0] = None
    bot._last_webhook_status[0] = "unknown"

    assert bot.webhook_status() == "ok"
    assert calls == ["getWebhookInfo"]


def test_tg_api_base_default_and_override(monkeypatch):
    monkeypatch.delenv("TG_API_BASE", raising=False)
    assert bot.tg_api_base() == "https://api.telegram.org"
    monkeypatch.setenv("TG_API_BASE", "https://tg-proxy.example.workers.dev/")
    assert bot.tg_api_base() == "https://tg-proxy.example.workers.dev"


def test_tg_call_uses_api_base_override(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TG_API_BASE", "https://tg-proxy.example.workers.dev")
    urls = []

    class FakeClient:
        def post(self, url, json=None):
            urls.append(url)

            class R:
                @staticmethod
                def json():
                    return {"ok": True}

            return R()

    monkeypatch.setattr(bot, "_get_tg_client", lambda: FakeClient())
    assert bot.tg_call("getMe") == {"ok": True}
    assert urls == ["https://tg-proxy.example.workers.dev/bot123:abc/getMe"]


def test_tg_call_swallows_json_decode_error(monkeypatch, caplog):
    """issue #114: resp.json() бросает json.JSONDecodeError (не httpx.HTTPError) —
    раньше это не ловилось tg_call и всплывало наверх (до немаскированного
    logger.exception в вебхуке, если сообщение содержит /bot<token>/)."""
    import json as json_mod

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:supersecrettoken")

    class FakeClient:
        def post(self, url, json=None):
            class R:
                @staticmethod
                def json():
                    raise json_mod.JSONDecodeError("Expecting value", "<html>not json</html>", 0)

            return R()

    monkeypatch.setattr(bot, "_get_tg_client", lambda: FakeClient())
    with caplog.at_level("WARNING"):
        # Раньше это падало наружу с json.JSONDecodeError; должно тихо вернуть None.
        assert bot.tg_call("getMe") is None
    assert "supersecrettoken" not in caplog.text
    assert "getMe failed" in caplog.text


def test_photo_caption_is_clipped_on_line_boundaries():
    """Регрессия: подпись к фото резалась по смещению в готовой HTML-разметке,
    то есть могла разорвать тег или пару <b>…</b>. Telegram на такой caption
    отвечает «can't parse entities», sendPhoto падает — и лот уходил вторым
    сообщением уже без фото."""
    from krisha.bot import _clip_html

    text = "\n".join(f"<b>строка {i}</b> и <a href=\"https://x/{i}\">ссылка</a>" for i in range(60))
    clipped = _clip_html(text, 300)

    assert len(clipped) <= 300
    assert clipped.endswith("…")
    assert clipped.count("<b>") == clipped.count("</b>"), "тег <b> не должен остаться открытым"
    assert clipped.count("<a ") == clipped.count("</a>"), "тег <a> не должен остаться открытым"
    # Короткий текст не трогаем вовсе.
    assert _clip_html("<b>коротко</b>", 300) == "<b>коротко</b>"


def test_track_rejects_out_of_range_listing_id(monkeypatch):
    r"""Регрессия: KRISHA_URL_RE ловит \d+ без ограничения длины, поэтому
    krisha.kz/a/show/999…9 давал питоновский bignum — sqlite3 поднимал
    OverflowError внутри хендлера, вебхук глотал его в общий except, и
    пользователь не получал вообще никакого ответа."""
    from krisha import bot

    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda m, **kw: calls.append((m, kw)) or {"ok": True})

    huge = "9" * 40
    bot.handle_update(
        {"message": {"chat": {"id": 7}, "text": f"/track https://krisha.kz/a/show/{huge}"}}
    )
    sent = [kw["text"] for m, kw in calls if m == "sendMessage"]
    assert sent and "Не похоже на id объявления" in sent[-1]


def test_format_reply_shows_model_band():
    """Справка обещает диапазон — ответ его показывает (продажа в млн, аренда в тыс/мес)."""
    sale = bot.format_reply(dict(SAMPLE_RESULT, fair_price_low=40_500_000, fair_price_high=52_400_000))
    assert "Диапазон оценки: <b>40.5–52.4 млн ₸</b>" in sale
    rent = bot.format_reply(dict(SAMPLE_RESULT, deal="arenda", actual_price=320_000, fair_price=290_000,
                                 fair_price_low=250_000, fair_price_high=340_000))
    assert "Диапазон оценки: <b>250–340 тыс ₸/мес</b>" in rent
    # нет интервала — нет и строки
    assert "Диапазон" not in bot.format_reply(SAMPLE_RESULT)


def test_track_rent_link_is_tracked_as_rent(tmp_path, monkeypatch):
    """/track на аренду: лот попадает в слежку с deal=arenda, ответ — в ₸/мес и про вечернюю проверку."""
    from krisha import tracking

    monkeypatch.setattr("krisha.subscriptions._push_to_github", lambda *a, **k: None)
    monkeypatch.setattr(tracking, "TRACKED_PATH", tmp_path / "tracked.json")
    monkeypatch.setattr(bot, "_track_listing_meta", lambda lid: (320_000, "2-комн в аренду", "arenda"))
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda m, **kw: calls.append((m, kw)) or {"ok": True})

    bot.handle_update({"message": {"chat": {"id": 9}, "text": "/track https://krisha.kz/a/show/1012607661"}})

    sent = [kw["text"] for m, kw in calls if m == "sendMessage"]
    assert sent and "Слежу" in sent[-1] and "320 000 ₸/мес" in sent[-1] and "вечернего" in sent[-1]
    assert tracking.list_tracked(9)["1012607661"]["deal"] == "arenda"

    bot.handle_update({"message": {"chat": {"id": 9}, "text": "/track"}})
    assert "320 тыс ₸/мес" in [kw["text"] for m, kw in calls if m == "sendMessage"][-1]


def test_track_meta_stores_rent_in_rent_db(tmp_path, monkeypatch):
    """Аренда с сайта ложится в базу аренды (не продажи) и возвращается с deal=arenda."""
    from krisha import config, db, predict
    from krisha.scraping import detail_parser

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "sale.db")
    monkeypatch.setattr(config, "RENT_DB_PATH", tmp_path / "rent.db")

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return "<html></html>"

    monkeypatch.setattr(predict, "user_client", lambda **kw: FakeClient())
    monkeypatch.setattr(detail_parser, "parse_detail",
                        lambda page, url: {"id": 1012607661, "price": 320_000, "deal": "arenda", "title": "2-комн"})
    stored = []
    monkeypatch.setattr(db, "upsert_listing", lambda listing, *a, **k: stored.append(k.get("db_path")))

    assert bot._track_listing_meta(1012607661) == (320_000, "2-комн", "arenda")
    assert stored == [tmp_path / "rent.db"]


def test_track_meta_finds_rent_in_rent_db_without_fetch(tmp_path, monkeypatch):
    """Лот уже есть в базе аренды — сайт не дёргаем."""
    from krisha import config, predict
    from krisha.db import init_db, price_bounds_for, upsert_listing

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "sale.db")
    monkeypatch.setattr(config, "RENT_DB_PATH", tmp_path / "rent.db")
    init_db(tmp_path / "rent.db")
    upsert_listing({"id": 555, "url": "https://krisha.kz/a/show/555", "title": "Аренда 1к",
                    "price": 250_000, "source": "test"}, db_path=tmp_path / "rent.db",
                   price_bounds=price_bounds_for("arenda"))
    monkeypatch.setattr(predict, "user_client", lambda **kw: (_ for _ in ()).throw(AssertionError("fetch")))

    assert bot._track_listing_meta(555) == (250_000, "Аренда 1к", "arenda")


def test_tg_call_error_log_has_no_chat_ids(monkeypatch, caplog):
    """Ответ ошибки Telegram бывает с parameters.migrate_to_chat_id — целиком
    в лог (он публичный в Actions) его писать нельзя."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")

    class FakeClient:
        def post(self, url, json=None):
            class R:
                @staticmethod
                def json():
                    return {"ok": False, "error_code": 400,
                            "description": "Bad Request: group chat was upgraded to a supergroup chat",
                            "parameters": {"migrate_to_chat_id": -1009876543210}}

            return R()

    monkeypatch.setattr(bot, "_get_tg_client", lambda: FakeClient())
    with caplog.at_level("WARNING"):
        bot.tg_call("sendMessage", chat_id=987654321, text="x")

    assert "400 Bad Request" in caplog.text
    assert "9876543210" not in caplog.text and "987654321" not in caplog.text


def test_format_reply_explains_reference_point_and_merged_age_label():
    """Вклады — от «типичной квартиры» модели: бот называет её цену; год
    постройки и возраст дома — одна подпись, как на сайте."""
    result = dict(
        SAMPLE_RESULT,
        factors_base=43_760_000,
        top_factors=[
            {"feature": "building_age", "impact": -0.08, "impact_pct": -7.7, "impact_tenge": -3_200_000},
            {"feature": "lat", "impact": 0.05, "impact_pct": 5.1, "impact_tenge": 2_000_000},
        ],
    )
    text = bot.format_reply(result)

    assert "от типичной квартиры — 43.8 млн ₸" in text
    assert "Возраст дома: -7.7% (-3.2 млн ₸)" in text
    assert "Расположение на карте" in text
    assert bot.FEATURE_RU["year_built"] == bot.FEATURE_RU["building_age"]


JARGON = ("лот", "Лот", "алерт", "Алерт")


def test_help_texts_speak_plain_russian():
    """Пользователь видит «объявление» и «уведомление», а не «лот» и «алерт»."""
    for text in (bot.HELP_TEXT, bot.ALERTS_HELP, bot.TRACK_HELP):
        assert not any(w in text for w in JARGON), text


def test_alerts_on_reply_speaks_plain_russian(monkeypatch):
    import krisha.subscriptions as subs
    sent = []
    monkeypatch.setattr(bot, "tg_call", lambda m, **kw: sent.append(kw) or {"ok": True})
    monkeypatch.setattr(subs, "set_subscription", lambda chat_id, flt: None)
    bot._handle_alerts_command(42, "/alerts_on 2к до 45млн")
    text = sent[-1]["text"]
    assert "Пришлю новые выгодные объявления" in text
    assert not any(w in text for w in JARGON), text


def test_verdicts_match_site_wording():
    """Вердикты называются как на сайте: «Выгодно», «В рынке», «Дорого»."""
    assert bot.VERDICT_RU["GOOD_DEAL"].startswith("🟢 Выгодно")
    assert bot.VERDICT_RU["FAIR"].startswith("🟡 В рынке")
    assert bot.VERDICT_RU["OVERPRICED"].startswith("🔴 Дорого")
    assert not any("ереплат" in v for v in bot.VERDICT_RU.values())
    # SAMPLE_RESULT — вердикт FAIR: в ответе новое название, сумма оценки подписана как раньше
    text = bot.format_reply(SAMPLE_RESULT)
    assert "🟡 В рынке" in text
    assert "⚖️ Справедливая цена:" in text
