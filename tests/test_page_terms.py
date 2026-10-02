"""Страница /terms: условия использования сайта и Telegram-бота baǵam.

Проверяем то, что легко потерять при правке вёрстки или кода: маршрут и
заголовки безопасности, живые подстановки точности модели, дату редакции,
название лицензии (берётся из файла LICENSE, а не из головы) и то, что
обещания на странице совпадают с кодом — лимит запросов и команды бота.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from krisha.api.app import app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _terms() -> str:
    return (STATIC / "terms.html").read_text(encoding="utf-8")


def _license_name() -> str:
    """Название лицензии — первая строка LICENSE (например, «Elastic License 2.0»)."""
    return (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()[0].strip()


def test_terms_route_serves_page_with_security_headers():
    resp = TestClient(app).get("/terms")

    assert resp.status_code == 200
    assert "Условия использования" in resp.text
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_terms_head_has_title_meta_canonical_and_design_css():
    html = _terms()

    assert "<title>Условия использования │ baǵam</title>" in html
    assert '<meta name="description"' in html
    assert 'href="/static/design.css"' in html
    assert 'rel="icon" type="image/svg+xml" href="/static/favicon.svg"' in html
    assert 'rel="canonical" href="https://dex719-krisha-fair-price.hf.space/terms"' in html
    assert 'property="og:title"' in html
    assert 'lang="ru"' in html


def test_terms_has_single_h1_and_toc_matches_sections():
    html = _terms()

    assert len(re.findall(r"<h1[\s>]", html)) == 1
    toc = re.findall(r'<div class="tocnav".*?</ol>', html, flags=re.S)
    assert toc, "нет оглавления"
    anchors = re.findall(r'href="#([a-z-]+)"', toc[0])
    assert len(anchors) >= 10
    for anchor in anchors:
        assert f'<section class="ds" id="{anchor}"' in html, f"в оглавлении есть #{anchor}, а раздела нет"
        assert f'id="h-{anchor}"' in html
    # оглавление не должно быть элементом <nav>: у него в design.css фиксированная шапка
    assert "<nav" not in toc[0]


def test_terms_shows_model_error_live_for_sale_and_rent():
    """Точность — живыми числами из /api/health: mape для продажи, rmape для аренды."""
    html = _terms()

    assert 'data-l="mape"' in html
    assert 'data-l="rmape"' in html
    # общий скрипт страницы действительно умеет заполнять оба хука
    assert "model_error_pct" in html
    assert "rent_model_error_pct" in html
    assert "[data-l=rmape]" in html


def test_terms_names_source_license_and_links_repo():
    html = _terms()
    license_name = _license_name()

    assert "krisha.kz" in html
    assert license_name in html, f"на странице нет названия лицензии из LICENSE: {license_name!r}"
    assert "https://github.com/Dex719/krisha-fair-price" in html
    assert "https://t.me/fairprice_kzbot" in html
    assert "https://t.me/Hopepe1" in html


def test_terms_has_edition_date():
    html = _terms()

    assert "2 октября 2026" in html
    assert 'datetime="2026-10-02"' in html


def test_terms_states_reference_nature_of_estimate():
    html = _terms()

    for phrase in (
        "Публичной офертой",
        "лицензированного оценщика",
        "Доходность валовая",
        "Налоги, простои",
    ):
        assert phrase in html, f"нет формулировки: {phrase}"
    # «если сдавать» описана как прогноз, а не обещание дохода
    assert "не обещание дохода" in html


def test_terms_rate_limit_statement_matches_code():
    """Страница обещает «до 15 запросов в минуту» — это дефолт из app.py."""
    source = (ROOT / "src" / "krisha" / "api" / "app.py").read_text(encoding="utf-8")
    m = re.search(r'RATE_LIMIT_TOTAL\s*=\s*_env_int\("RATE_LIMIT_PER_WINDOW",\s*(\d+)\)', source)
    window = re.search(r'RATE_WINDOW_S\s*=\s*float\(_env_int\("RATE_LIMIT_WINDOW_S",\s*(\d+)\)\)', source)

    assert m and window, "не нашли дефолтные лимиты в app.py"
    assert window.group(1) == "60", "окно лимита больше не минута — поправьте текст страницы"
    assert f"до {m.group(1)} запросов в минуту" in _terms()
    assert "429" in _terms()


def test_terms_bot_commands_exist_in_bot():
    """Все команды, которые условия называют, действительно есть в боте."""
    html = _terms()
    bot = (ROOT / "src" / "krisha" / "bot.py").read_text(encoding="utf-8")

    for cmd in ("/track", "/untrack", "/alerts_on", "/alerts_off"):
        assert cmd in html, f"условия не упоминают {cmd}"
        assert cmd in bot, f"в боте нет {cmd}"
    assert "/untrack all" in html and "/untrack all" in bot


def _bot_section() -> str:
    m = re.search(r'<section class="ds" id="bot".*?</section>', _terms(), flags=re.S)
    assert m, "нет раздела про бота"
    return m.group(0)


def test_terms_bot_section_numbers_match_code():
    """Числа в описании бота (лимиты, пороги) берутся из кода, а не из головы."""
    from krisha.alerts import MAX_DEALS_PER_CHAT
    from krisha.bot import CAPTION_LIMIT
    from krisha.config import MAX_TRUSTED_DELIST_LAG_DAYS
    from krisha.text_parse import MIN_TEXT_LEN
    from krisha.tracking import MAX_TRACKED_PER_CHAT

    section = _bot_section()
    text_parse = (ROOT / "src" / "krisha" / "text_parse.py").read_text(encoding="utf-8")

    assert f"не больше чем за {MAX_TRACKED_PER_CHAT} объявлениями" in section
    assert f"Короче {MIN_TEXT_LEN} символов" in section
    assert f"не больше {MAX_DEALS_PER_CHAT} объявлений" in section
    assert f"ограничена {CAPTION_LIMIT} символами" in section
    assert MAX_TRUSTED_DELIST_LAG_DAYS == 7, "порог снятия изменился — поправьте «семи дней» на странице"
    assert "дольше семи дней" in section
    # в Gemini уходит начало текста: «до 3000 символов» — это срез в text_parse
    assert "[:3000]" in text_parse and "до 3000 символов" in section


def test_terms_bot_section_covers_every_command_of_the_bot():
    """Раздел про бота называет все команды, которые бот обрабатывает."""
    section = _bot_section()
    bot = (ROOT / "src" / "krisha" / "bot.py").read_text(encoding="utf-8")

    for cmd in ("/start", "/help", "/track", "/untrack", "/untrack all", "/alerts", "/alerts_on", "/alerts_off"):
        assert cmd in section, f"раздел про бота не упоминает {cmd}"
        assert cmd.split()[0] in bot, f"в боте нет {cmd}"
    # оба режима оценки и ссылки на разделы страницы работают
    for anchor in ("#reference", "#limits", "#availability", "#yield"):
        assert f'href="{anchor}"' in section
        assert f'id="{anchor[1:]}"' in _terms()
    assert 'href="/privacy#bot"' in section


def test_terms_links_between_documents_and_footer():
    html = _terms()

    assert 'href="/privacy"' in html
    assert 'href="/about"' in html
    assert 'href="/terms" aria-current="page"' in html  # подвал: текущий документ


def test_terms_has_no_external_cdn_or_fonts():
    html = _terms()

    for host in (
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "cdnjs.cloudflare.com",
        "cdn.jsdelivr.net",
        "unpkg.com",
        "code.jquery.com",
        "cdn.tailwindcss.com",
    ):
        assert host not in html, f"внешний ресурс {host}"
    # ни одного внешнего скрипта или стиля в разметке
    assert not re.search(r'<script[^>]+src="https?://', html)
    assert not re.search(r'<link[^>]+rel="stylesheet"[^>]+href="https?://', html)
    assert "/static/fonts/" in html


def test_terms_does_not_make_legal_claims_about_scraping():
    """Условия не рассуждают о законности сбора и не придумывают юрисдикций."""
    text = _terms().lower()

    for word in ("законно ли", "законность", "федеральн", "гк рф", "гк рк", "роскомнадзор"):
        assert word not in text, f"неуместная формулировка: {word}"
