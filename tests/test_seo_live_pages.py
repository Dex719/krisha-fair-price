"""SEO-аудит 05.10.2026: живые цифры в разметке, JSON-LD, аренда со своим
canonical, robots/llms.txt, редиректы со слэшем и счётчики посещаемости из окружения.

Разметку проверяем на ответах сервера (TestClient), а снимок данных подменяем:
живые числа приходят из базы, которой в тестах может не быть.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from krisha.api import app as app_module
from krisha.api import live_pages, site_analytics
from krisha.api.app import CSP, app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
NB = " "
RAW = {"accept-encoding": "identity", "accept": "text/html"}

STATS = {
    "total_listings": 44026,
    "median_price": 42000000,
    "median_ppsm": 750000,
    "updated_at": "2026-10-05T09:55:43+00:00",
    "by_district": [
        {"district": "Медеуский", "n": 4300, "median_ppsm": 937395, "median_price": 65000000},
        {"district": "Бостандыкский", "n": 10339, "median_ppsm": 916230, "median_price": 63800000},
        {"district": "Ауэзовский", "n": 6000, "median_ppsm": 740000, "median_price": 38000000},
        {"district": "Турксибский", "n": 3000, "median_ppsm": 600000, "median_price": 30000000},
    ],
}
RENT = {
    "total_listings": 8649,
    "median_rent": 320000,
    "updated_at": "2026-10-04T19:38:17+00:00",
    "by_rooms": [{"rooms": 1, "n": 2698, "median_rent": 227499}, {"rooms": 2, "n": 3797, "median_rent": 350000}],
    "by_district": [{"district": "Бостандыкский", "n": 2388, "median_rent": 500000, "gross_yield_pct": 9.7}],
}
HEALTH = {
    "model_error_pct": 7.0,
    "model_median_error_pct": 5.1,
    "rent_model_error_pct": 10.9,
    "model_error_ci_pct": [6.9, 7.2],
    "model_r2": 0.955,
    "model_mae": 3986162.0,
}


@pytest.fixture
def live(monkeypatch):
    """Страницы, собранные со снимком данных; после теста — как были."""
    snap = app_module._LiveSnapshot(STATS, RENT, live_pages.live_values(STATS, HEALTH))
    app_module._render_pages(snap)
    yield TestClient(app)
    app_module._render_pages(app_module._LIVE)


def _ld(html: str) -> list[dict]:
    return [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.S)]


# --------------------------------------------------------------- форматы


def test_formats_match_site_js():
    v = live_pages.live_values(STATS, HEALTH)

    assert v["total"] == f"44{NB}026"
    assert v["ppsmk"] == f"750{NB}тыс{NB}₸/м²"
    assert v["medpricem"] == f"42,0{NB}млн{NB}₸"
    assert v["mape"] == "7,0%" and v["rmape"] == "10,9%" and v["mdape"] == "5,1%"
    assert v["mapeci"] == ", 95% ДИ 6,9–7,2%"
    assert v["r2"] == "0.955" and v["mae"] == "3,99"
    assert v["upd"] == "05.10.2026"
    # toFixed в JS округляет половину вверх, round() в Python — к чётному
    assert live_pages.fmt(0.25) == "0,3" and live_pages.fmt(-0.04) == "0,0"
    assert live_pages.plural(21, ("а", "б", "в")) == "а" and live_pages.plural(12, ("а", "б", "в")) == "в"


def test_fill_live_replaces_only_known_keys():
    page = ('<b data-l="mape" data-count>7,3%</b> <span data-l="total">21 113</span>&nbsp;'
            '<span data-l="totalw" data-forms="объявлением,объявлениями,объявлениями">x</span> <i data-l="age">ежедневно</i>')
    out = live_pages.fill_live(page, {"mape": "7,0%", "total": "44 021"}, total=44021)

    assert '<b data-l="mape" data-count>7,0%</b>' in out
    assert '<span data-l="total">44 021</span>' in out
    assert ">объявлением</span>" in out
    assert '<i data-l="age">ежедневно</i>' in out


# --------------------------------------------------------------- страницы


def test_pages_carry_live_numbers_instead_of_stale_snapshot(live):
    home = live.get("/", headers=RAW).text
    about = live.get("/about", headers=RAW).text
    terms = live.get("/terms", headers=RAW).text

    assert f'<span data-l="total">44{NB}026</span>' in home
    assert '<b data-l="mape" data-count>7,0%</b>' in home
    assert "21 113" not in home and "21 113" not in about
    assert '<b class="big" data-l="mape">7,0%</b>' in about
    assert "7,6%" not in terms
    # столбики районов — из снимка, а не прошлой сборки
    assert '<span class="dpx">937<i>тыс ₸/м²</i></span><span class="dnm">Медеуский</span>' in home
    assert "<b>+25%</b> к медиане" in home
    assert len(re.findall(r'<div class="dcol (?:hi|lo)">', home)) == 4


def test_without_snapshot_pages_stay_as_files():
    app_module._render_pages(None)
    try:
        html = TestClient(app).get("/", headers=RAW).text
    finally:
        app_module._render_pages(app_module._LIVE)
    assert 'data-l="total">' in html


def test_index_has_valid_site_json_ld():
    """WebSite + Organization. WebApplication без рейтинга Google считает ошибкой
    («Программные приложения» в Search Console), а рейтинг выдумывать нельзя."""
    html = TestClient(app).get("/", headers=RAW).text
    graph = _ld(html)[0]["@graph"]
    types = {node["@type"] for node in graph}

    assert types == {"WebSite", "Organization"}
    site = next(n for n in graph if n["@type"] == "WebSite")
    assert "Алматы" in site["description"] and "krisha.kz" in site["description"]
    assert all(n["url"] == "https://bagam.info/" for n in graph)


def test_about_x40_follows_live_mape(live):
    about = live.get("/about", headers=RAW).text
    # 7,0% × 40 млн ₸ = 2,8 млн ₸ — рядом с ошибкой модели, а не «3,0» от прошлых 7,6%
    assert '±<span data-l="x40" data-x40>2,8</span>' in about


def test_faq_answers_keep_paragraph_boundaries(live):
    html = live.get("/bot", headers=RAW).text
    faq = next(b for b in _ld(html) if b.get("@type") == "FAQPage")
    for item in faq["mainEntity"]:
        assert not re.search(r"[а-я][.!?][А-Я]", item["acceptedAnswer"]["text"]), item

    assert live_pages._text("<p>Раз.</p><p>Два <b>7,0%</b>.</p>") == "Раз. Два 7,0%."


@pytest.mark.parametrize("path", ["/about", "/bot"])
def test_faq_page_markup_matches_visible_questions(path, live):
    html = live.get(path, headers=RAW).text
    faq = next(b for b in _ld(html) if b.get("@type") == "FAQPage")
    questions = [q["name"] for q in faq["mainEntity"]]
    visible = re.findall(r'<button class="q"[^>]*>(.*?)<span class="qi">', html)

    assert questions == visible and len(questions) >= 4
    # числа в ответах — те же, что видит человек (после подстановки снимка)
    if path == "/bot":
        answer = faq["mainEntity"][-1]["acceptedAnswer"]["text"]
        assert "7,0%" in answer and "10,9%" in answer


def test_rent_mode_has_own_title_canonical_and_sitemap_entry(live):
    rent = live.get("/stats?mode=rent", headers=RAW).text
    sale = live.get("/stats", headers=RAW).text
    sitemap = live.get("/sitemap.xml").text

    assert '<link rel="canonical" href="https://bagam.info/stats?mode=rent">' in rent
    assert "<title>Аренда квартир в Алматы" in rent and "<title>Цены на квартиры в Алматы" in sale
    # без JS режим выбирает CSS по html[data-mode]: краулер видит тексты аренды
    assert '<html data-mode="rent" lang="ru">' in rent and "data-mode=" not in sale.split(">", 2)[1]
    assert '<div class="nsdata">' in rent and 'class="rnote nsdata"' not in rent
    assert "<loc>https://bagam.info/stats?mode=rent</loc>" in sitemap
    # без JS — сводка рынка: аренда первой на её адресе, продажа — на своём
    assert "Аренда квартир в Алматы на 05.10.2026" in rent or "Аренда квартир в Алматы на 04.10.2026" in rent
    assert rent.index("Аренда квартир в Алматы на") < rent.index("Продажа квартир в Алматы на")
    assert sale.index("Продажа квартир в Алматы на") < sale.index("Аренда квартир в Алматы на")
    assert "<th scope=\"row\">Медеуский</th>" in sale


def test_llms_txt(live):
    resp = live.get("/llms.txt")

    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/plain")
    assert resp.text.startswith("# baǵam (bagam.info)\n\n> ")
    assert "44 026 активных объявлений" in resp.text and "7,0%" in resp.text
    assert "(https://bagam.info/stats?mode=rent)" in resp.text
    assert live.head("/llms.txt").status_code == 200


# --------------------------------------------------------------- обход и индекс


def test_robots_opens_market_data_and_closes_service_urls():
    robots = TestClient(app).get("/robots.txt").text

    for line in ("Allow: /api/stats", "Allow: /api/health", "Disallow: /api/", "Disallow: /docs",
                 "Disallow: /openapi.json", "Disallow: /tg/"):
        assert line + "\n" in robots, line
    assert "Clean-param: utm_source&" in robots


def test_service_urls_are_noindex_and_pages_are_not():
    client = TestClient(app)

    for path in ("/api/health", "/openapi.json", "/docs", "/livez"):
        assert client.get(path).headers.get("x-robots-tag") == "noindex", path
    page = client.get("/", headers=RAW)
    assert "x-robots-tag" not in page.headers
    assert page.headers["permissions-policy"].startswith("camera=()")


def test_trailing_slash_is_a_relative_301_not_a_trip_to_the_space():
    client = TestClient(app)

    for path, target in (("/about/", "/about"), ("/stats/?mode=rent", "/stats?mode=rent"), ("/bot//", "/bot")):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 301, path
        assert resp.headers["location"] == target, path
    # API со слэшем — честный 404, а не редирект POST-а
    assert client.get("/api/health/", follow_redirects=False).status_code == 404


def test_static_html_copies_are_404_and_favicon_ico_exists():
    client = TestClient(app)

    assert client.get("/static/about.html", headers=RAW).status_code == 404
    # служебный ключ варианта аренды через %3F — тоже не копия страницы
    assert client.get("/static/stats.html%3Fmode=rent", headers=RAW).status_code == 404
    assert client.get("/static/design.css").status_code == 200
    ico = client.get("/favicon.ico")
    assert ico.status_code == 200 and ico.headers["content-type"] == "image/x-icon"
    assert ico.content[:4] == b"\x00\x00\x01\x00"


# --------------------------------------------------------------- счётчики


def test_no_counters_without_env(monkeypatch):
    for name in ("YANDEX_METRIKA_ID", site_analytics.GA_ENV,
                 site_analytics.YANDEX_VERIFICATION_ENV, site_analytics.GOOGLE_VERIFICATION_ENV):
        monkeypatch.delenv(name, raising=False)
    app_module._build_assets()
    resp = TestClient(app).get("/", headers=RAW)

    assert "analytics.js" not in resp.text and "verification" not in resp.text
    assert resp.headers["content-security-policy"] == CSP


def test_counters_and_verification_come_from_env(monkeypatch):
    # номер Метрики — от прежней версии: больше не читается и в разметку не попадает
    monkeypatch.setenv("YANDEX_METRIKA_ID", "98765432")
    monkeypatch.setenv(site_analytics.GA_ENV, "G-ABC123XYZ")
    monkeypatch.setenv(site_analytics.YANDEX_VERIFICATION_ENV, "a1b2c3d4e5f6a7b8")
    monkeypatch.setenv(site_analytics.GOOGLE_VERIFICATION_ENV, "Zx_9-verify-token")
    try:
        app_module._build_assets()
        client = TestClient(app)
        home = client.get("/", headers=RAW)
        about = client.get("/about", headers=RAW).text
    finally:
        monkeypatch.undo()
        app_module._build_assets()

    tag = re.search(r'<script src="/static/js/analytics\.js\?v=[0-9a-f]+" defer data-ga="G-ABC123XYZ"></script>', home.text)
    assert tag and tag.start() < home.text.index("</head>")
    assert 'data-ga="G-ABC123XYZ"' in about and "data-ym" not in home.text
    assert '<meta name="yandex-verification" content="a1b2c3d4e5f6a7b8">' in home.text
    assert '<meta name="google-site-verification" content="Zx_9-verify-token">' in home.text
    assert "verification" not in about, "коды подтверждения — только на главной"
    csp = home.headers["content-security-policy"]
    for source in ("https://www.googletagmanager.com", "https://*.google-analytics.com",
                   "https://*.analytics.google.com"):
        assert source in csp, source
    assert "yandex" not in csp
    script_src = next(d for d in csp.split("; ") if d.startswith("script-src "))
    assert "'self'" in script_src and "https://www.googletagmanager.com" in script_src


def test_counters_stay_off_inside_telegram_and_without_ads():
    """Mini App: в хеше адреса #tgWebAppData с профилем Telegram — внутри Telegram
    счётчик не стартует. GA4 — без рекламных функций."""
    script = (STATIC / "js" / "analytics.js").read_text(encoding="utf-8")
    guard = script.index("/tgWebApp/i.test(")

    assert guard < script.index("gtag('config'")
    assert "TelegramWebviewProxy" in script[guard - 200:guard + 200]
    assert "allow_google_signals: false" in script and "allow_ad_personalization_signals: false" in script


def test_refresh_live_pages_is_switched_by_env(monkeypatch):
    snap = app_module._LiveSnapshot(STATS, RENT, live_pages.live_values(STATS, HEALTH))
    monkeypatch.setattr(app_module, "_live_snapshot", lambda: snap)
    try:
        monkeypatch.setenv("KRISHA_LIVE_PAGES", "0")
        app_module._refresh_live_pages()
        assert app_module._LIVE is None

        monkeypatch.setenv("KRISHA_LIVE_PAGES", "1")
        app_module._refresh_live_pages()
        assert app_module._LIVE is snap
        assert f"44{NB}026" in app_module._ASSETS["index.html"].raw.decode("utf-8")
    finally:
        app_module._LIVE = None
        app_module._render_pages(None)


def test_garbage_in_env_is_ignored():
    cfg = site_analytics.from_env({
        site_analytics.GA_ENV: 'UA-1234-1"><script>',
        site_analytics.YANDEX_VERIFICATION_ENV: "short",
    })
    assert cfg == site_analytics.Config()


def test_extend_csp_adds_sources_and_new_directives():
    out = site_analytics.extend_csp("default-src 'self'; img-src 'self' data:",
                                    {"img-src": ("https://a.example", "data:"), "frame-src": ("'self'", "blob:")})
    assert out == "default-src 'self'; img-src 'self' data: https://a.example; frame-src 'self' blob:"
