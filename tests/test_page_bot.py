"""Страница /bot: описывает только то, что бот действительно умеет."""

import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from krisha import bot
from krisha.alerts import MAX_DEALS_PER_CHAT
from krisha.api.app import app
from krisha.config import MAX_TRUSTED_DELIST_LAG_DAYS
from krisha.text_parse import MIN_TEXT_LEN
from krisha.tracking import MAX_TRACKED_PER_CHAT

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
BOT_SRC = (ROOT / "src" / "krisha" / "bot.py").read_text(encoding="utf-8")


def _page() -> str:
    return (STATIC / "bot.html").read_text(encoding="utf-8")


class _Text(HTMLParser):
    """Видимый текст страницы без тегов, скриптов и стилей."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _main_text() -> str:
    html = _page()
    main = html[html.index('<main id="main">'):html.index("</main>")]
    parser = _Text()
    parser.feed(main)
    return re.sub(r"\s+", " ", " ".join(parser.parts))


def _commands_in_text() -> set[str]:
    # «/cmd» не после буквы, цифры, «/» или «.»: так не цепляются куски ссылок
    # вроде krisha.kz/a/show/… и t.me/fairprice_kzbot
    return set(re.findall(r"(?<![\w/.:@=-])/([a-z][a-z_]*)\b", _main_text()))


def test_bot_page_route_serves_page_with_security_headers():
    resp = TestClient(app).get("/bot")

    assert resp.status_code == 200
    assert "Telegram-бот baǵam" in resp.text
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_bot_page_chrome_meta_and_single_h1():
    html = _page()

    assert "<title>Telegram-бот baǵam" in html
    assert "│ baǵam</title>" in html
    assert '<meta name="description"' in html
    assert 'href="/static/design.css"' in html
    assert 'rel="canonical" href="https://dex719-krisha-fair-price.hf.space/bot"' in html
    assert len(re.findall(r"<h1[ >]", html)) == 1
    assert "FairPrice" not in html


def test_bot_page_links_to_the_real_bot():
    html = _page()

    assert 'href="https://t.me/fairprice_kzbot"' in html
    assert "@fairprice_kzbot" in html
    assert "Открыть в Telegram" in html
    assert "Telegram-бот умеет три вещи" in html


def test_every_command_on_the_page_exists_in_bot_source():
    found = _commands_in_text()

    # страница должна описывать весь набор, а не его часть
    assert {"start", "help", "track", "untrack", "alerts", "alerts_on", "alerts_off"} <= found
    for cmd in sorted(found):
        assert f'"/{cmd}"' in BOT_SRC, f"/{cmd} описана на странице, но бот её не знает"


def test_commands_list_matches_the_bot_help():
    # то, что бот сам обещает в /help, страница не должна расходиться с ним
    for cmd in ("/track", "/alerts", "/alerts_on"):
        assert cmd in bot.HELP_TEXT
    for cmd in ("/untrack", "/alerts_off"):
        assert cmd in bot.TRACK_HELP + bot.ALERTS_HELP


def test_page_numbers_follow_the_code():
    text = _main_text()

    assert f"До {MAX_TRACKED_PER_CHAT} лотов" in text
    assert f"До {MAX_DEALS_PER_CHAT} в одном сообщении" in text
    assert f"от {MIN_TEXT_LEN} символов" in text
    assert f"больше {MAX_TRUSTED_DELIST_LAG_DAYS} дней" in text


def test_page_names_sale_and_rent_and_keeps_live_numbers():
    html = _page()
    text = _main_text()

    assert "аренд" in text and "продаж" in text
    assert "Справедливая аренда" in text
    # цифры точности — живые, из /api/health
    assert 'data-l="mape"' in html and 'data-l="rmape"' in html


def test_page_links_to_neighbours_and_has_faq():
    html = _page()

    for href in ('href="/"', 'href="/stats"', 'href="/rent"', 'href="/privacy"', 'href="/about"'):
        assert href in html
    assert 'class="faq"' in html and html.count('class="qa"') >= 4
    assert 'aria-controls="qa0"' in html and 'id="qa0"' in html
    # Telegram-макет берётся из общего design.css
    assert 'class="iph"' in html and "tgb in" in html and "tgb out" in html


def test_page_has_no_external_assets_or_promises():
    html = _page()

    assert not re.search(r'<(?:script|link|img|iframe|source)\b[^>]*\b(?:src|href)="(?:https?:)?//(?!t\.me|dex719-krisha-fair-price\.hf\.space)', html)
    for host in ("googleapis", "gstatic", "cdn.", "unpkg", "jsdelivr", "cdnjs"):
        assert host not in html
    for promise in ("скоро добавим", "скоро появится", "в разработке"):
        assert promise not in html.lower()


# ---------------------------------------------------------------------------
# Сверка с кодом: примеры на странице должны быть тем, что бот реально пишет
# ---------------------------------------------------------------------------

def _plain(markup: str) -> str:
    """HTML сообщения бота → текст, как его видит человек в чате."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", markup))).strip()


def _page_lines(text: str) -> list[str]:
    """Непустые строки сообщения бота без разметки."""
    return [line for line in (_plain(p) for p in text.split("\n")) if line]


def _squash(text: str) -> str:
    """Без пробелов: теги <b> и <span> в вёрстке разрывают текст пробелами, которых в чате нет."""
    return re.sub(r"\s+", "", text)


def _assert_on_page(message: str) -> None:
    page = _squash(_main_text())
    for line in _page_lines(message):
        assert _squash(line) in page, f"в чате на странице нет строки бота: {line!r}"


def test_hero_chat_is_what_format_reply_produces():
    result = {
        "title": "2-комнатная квартира · 49 м² · 5/9 этаж",
        "address": "Алматы, Алатауский р-н, мкр Алтын орда",
        "actual_price": 27_500_000,
        "fair_price": 28_700_000,
        "verdict": "GOOD_DEAL",
        "diff_pct": -4.2,
        "top_factors": [
            {"feature": "district_ppsm", "impact": 1, "impact_pct": 5.6, "impact_tenge": 1_600_000},
            {"feature": "floor", "impact": -1, "impact_pct": -2.1, "impact_tenge": -600_000},
            {"feature": "area", "impact": 1, "impact_pct": 1.5, "impact_tenge": 400_000},
        ],
    }

    _assert_on_page(bot.format_reply(result))


def test_reply_block_examples_follow_format_reply():
    result = {
        "fair_price": 28_700_000,
        "liquidity": {"band_median_days": 35, "band": "near", "band_sample": 112},
        "rental_yield": {"monthly_rent": 210_000, "gross_yield_pct": 9.2, "district_yield_pct": 7.9},
        "analogs": [{"title": "2-комнатная квартира · 47 м²", "url": "https://krisha.kz/a/show/1",
                     "price": 27_900_000}],
    }
    reply = bot.format_reply(result)

    for line in _page_lines(reply):
        if line.startswith(("⏳", "📈", "•")):
            assert _squash(line) in _squash(_main_text()), f"пример блока расходится с ботом: {line!r}"
    assert "⚖️ Справедливая цена: 28 700 000 ₸" in _plain(reply)


def test_alerts_chat_is_what_format_alert_produces():
    from krisha.alerts import format_alert

    deals = [
        {"title": "2-комнатная квартира · 52 м² · 6/9 этаж", "url": "https://krisha.kz/a/show/1",
         "price": 38_400_000, "fair_price": 41_200_000, "diff_pct": -6.8,
         "district": "Bostandykskiy_r-n"},
        {"title": "2-комнатная квартира · 58 м² · 3/5 этаж", "url": "https://krisha.kz/a/show/2",
         "price": 44_000_000, "fair_price": 47_900_000, "diff_pct": -8.1,
         "district": "Bostandykskiy_r-n"},
    ]

    _assert_on_page(format_alert(deals))


def test_track_chat_is_what_tracking_writes(monkeypatch):
    from krisha import tracking

    title = "2-комнатная квартира · 49 м² · 5/9 этаж"
    # событие «цена изменилась», которое после рескрейпа шлёт scripts/send_alerts.py
    row = {"is_active": 1, "price": 26_900_000, "title": None, "first_seen": None,
           "last_seen": None, "delisted_at": None, "url": "https://krisha.kz/a/show/1012607661"}
    event = tracking._lot_event("1012607661", {"price": 27_500_000, "title": title}, row)
    _assert_on_page(event)

    # ответ бота на /track
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: sent.append((method, kw)) or {"ok": True})
    monkeypatch.setattr(bot, "_track_listing_meta", lambda listing_id: (27_500_000, title))
    monkeypatch.setattr(tracking, "add_tracked", lambda *a, **k: (True, None))
    bot._handle_track_command(1, "/track https://krisha.kz/a/show/1012607661")
    texts = [kw["text"] for method, kw in sent if method == "sendMessage"]
    assert len(texts) == 1
    _assert_on_page(texts[0])


def test_alerts_on_confirmation_is_what_bot_replies(monkeypatch):
    from krisha import subscriptions

    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "tg_call", lambda method, **kw: sent.append((method, kw)) or {"ok": True})
    monkeypatch.setattr(subscriptions, "set_subscription", lambda chat_id, flt: None)
    bot._handle_alerts_command(1, "/alerts_on 2к до 45млн бостандыкский")

    texts = [kw["text"] for method, kw in sent if method == "sendMessage"]
    assert len(texts) == 1
    _assert_on_page(texts[0])


def test_filter_examples_are_read_by_the_bot_as_the_page_says():
    from krisha.subscriptions import parse_filters

    html = _page()
    groups = dict(re.findall(r'<div class="bf"><h4>(.*?)</h4>.*?<div class="bchips">(.*?)</div>', html))
    assert set(groups) == {"Комнаты", "Бюджет", "Район"}
    chips = {name: re.findall(r"<code>(.*?)</code>", body) for name, body in groups.items()}

    for chip in chips["Комнаты"]:
        flt = parse_filters(chip)
        assert flt["rooms"] == 2 and flt["max_price"] is None, chip
    for chip in chips["Бюджет"]:
        flt = parse_filters(chip)
        assert flt["max_price"] and flt["rooms"] is None, f"{chip!r} бот читает не как бюджет"
    for chip in chips["Район"]:
        assert parse_filters(chip)["district"], f"{chip!r} бот не узнаёт как район"
    # запятая в «38,5» режет число на 38 и 5 (бюджет 38 млн и 5 комнат), поэтому дробь — через точку
    assert "38.5" in chips["Бюджет"] and "38,5" not in chips["Бюджет"]
    assert parse_filters("38.5")["max_price"] == 38_500_000
    assert parse_filters("38,5")["rooms"] == 5


def test_deep_links_and_district_count_follow_the_code():
    from krisha.config import DISTRICT_RU

    text = _main_text()

    assert "start=track_" in text and 'payload.startswith("track_")' in BOT_SRC
    slugs = re.findall(r"start=market_([a-z]+)", text)
    assert slugs and all(slug in bot.MARKET_DISTRICT_SLUGS for slug in slugs)
    assert "восемь" in text and len(DISTRICT_RU) == 8


def test_headings_are_unique_and_not_glued_by_br():
    html = _page()
    main = html[html.index('<main id="main">'):html.index("</main>")]
    headings = re.findall(r"<h([1-4])\b[^>]*>(.*?)</h\1>", main, flags=re.S)

    texts = [_plain(body) for _, body in headings]
    assert len(texts) == len(set(texts)), "заголовки повторяются — читалка экрана их не различит"
    for _, body in headings:
        # «который<br>знает» читалка склеит в «которыйзнает»
        assert not re.search(r"[^\s>]<br>", body), body


def test_fallback_numbers_agree_and_faq_does_not_wait_for_animation_library():
    html = _page()

    for key in ("mape", "rmape"):
        shown = set(re.findall(rf'data-l="{key}">([^<]*)<', html))
        assert len(shown) == 1, f"запасные значения {key} расходятся: {shown}"
    # закрытый ответ не должен держать фокус на своих ссылках
    assert ".inert=true" in html
    # вопросы открываются сразу, а не после загрузки GSAP (bagamBoot ждёт до 7 с)
    assert html.index("pane.inert=true") < html.index("bagamBoot(function(){")
    # HOME_URL нигде не объявлен: обращение к нему — ReferenceError внутри Telegram
    assert "HOME_URL" not in html
