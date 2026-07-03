"""Тесты Telegram-бота: форматирование и обработка апдейтов (без сети)."""

from krisha import bot

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


def test_handle_update_no_url_hint(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "сколько стоит квартира?"}})
    assert calls[0][0] == "sendMessage"
    assert "Не вижу ссылки" in calls[0][1]["text"]


def test_handle_update_predicts_and_sends_photo(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: calls.append((method, kw)) or {"ok": True})
    monkeypatch.setattr(bot, "predict_from_url", lambda url: SAMPLE_RESULT)
    bot.handle_update({"message": {"chat": {"id": 42}, "text": "https://krisha.kz/a/show/123"}})
    methods = [m for m, _ in calls]
    assert "sendChatAction" in methods
    assert "sendPhoto" in methods
    photo_call = [kw for m, kw in calls if m == "sendPhoto"][0]
    assert photo_call["photo"] == "https://example.com/p1.jpg"
    assert "48 120 000 ₸" in photo_call["caption"]


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
