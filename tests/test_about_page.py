"""Acceptance checks for issue #81 about page."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from krisha.api.app import CSP, app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _with_shared(name: str) -> str:
    """Страница + общий static/js/site.js: живые цифры подставляет он."""
    html = _static(name)
    assert '<script src="/static/js/site.js" defer' in html
    return html + _static("js/site.js")


def _assert_security_headers(resp):
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "font-src 'self'" in csp
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_about_page_route_serves_file_with_security_headers():
    client = TestClient(app)
    resp = client.get("/about")

    assert resp.status_code == 200
    assert "Как мы считаем справедливую цену квартиры" in resp.text
    _assert_security_headers(resp)


def test_about_page_uses_bagam_chrome_meta_and_live_sources():
    html = _with_shared("about.html")

    assert "Как мы считаем справедливую цену квартиры │ baǵam" in html
    assert '<meta name="description"' in html
    assert 'href="/static/design.css"' in html
    assert 'rel="icon" type="image/svg+xml" href="/static/favicon.svg"' in html
    assert 'href="/about"' in html and 'href="/stats"' in html
    assert "/api/stats" in html
    assert "/api/health" in html
    assert "model_error_pct" in html
    assert "m3.css" not in html
    assert "FairPrice" not in html
    assert "Manrope" not in html
    assert "fonts.gstatic.com" not in html


def test_about_page_has_no_frozen_numbers_for_live_metrics():
    """Точность модели показывается живыми числами из /api/health."""
    html = _with_shared("about.html")

    for hook in ('data-l="mape"', 'data-l="mdape"', 'data-l="rmape"', 'data-l="total"', 'data-l="age"',
                 'data-l="ppsmk"'):
        assert hook in html, f"нет живой подстановки {hook}"
    # ширина интервала зависит от лота, поэтому фиксированного числа быть не должно
    assert "±9,5%" not in html
    assert "model_error_pct" in html
    assert "цифры из последнего успешного обновления" in html
    # цифры вне [data-l] — вшитые руками: такие разъезжались с метой (7,3% при живых 7,6%)
    outside = re.sub(r'data-l="[a-z]+">[^<]*<', "", _main())
    for frozen in ("7,3%", "7,6%", "5,5%", "11,0%", "742 тыс", "0.937", "3,69"):
        assert frozen not in outside, f"вшитая цифра {frozen} вместо живой"


def _main() -> str:
    html = _static("about.html")
    return html[html.index('<main id="main">'):html.index("</main>")]


def test_about_does_not_duplicate_the_bot_page():
    """Про бота — карточка «Куда дальше», а не пересказ /bot."""
    main = _main()

    assert "Telegram-бот умеет три вещи" not in main
    assert 'href="/bot"' in main
    for promise in ("скоро добавим", "скоро появится", "в разработке"):
        assert promise not in main.lower()


def test_about_css_and_nav_are_connected():
    css = _static("design.css")
    about = _static("about.html")
    index = _static("index.html")
    market = _static("stats.html")

    assert 'class="prose"' in about   # текстовые блоки страницы на месте
    assert ".shead" in css       # общая шапка секции — в общем файле
    assert 'href="/about"' in index
    assert 'href="/about"' in market


def test_health_exposes_model_error_pct_without_csp_changes():
    client = TestClient(app)
    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert "model_error_pct" in resp.json()
    assert "fonts.googleapis.com" not in CSP
    assert "fonts.gstatic.com" not in CSP


def test_about_accuracy_is_two_plain_numbers_without_promises():
    """issue #158 и ревью сайта: голый процент не должен читаться как обещание.

    Точность — два понятных числа (медианная и средняя ошибка) и ошибка аренды;
    без R², MAE и подписи «доля ошибки под 10%» над медианой. Обещания про будущие
    объявления (подпись tvnote из site.js) на странице больше нет.
    """
    main = _main()

    assert "В половине случаев" in main and 'data-l="mdape"' in main
    assert "Доля ошибки под 10%" not in main
    for gone in ('data-l="r2"', 'data-l="mae"', 'data-l="tvnote"', "MAPE", "MdAPE", "R²"):
        assert gone not in main, f"на странице осталась метрика для специалистов: {gone}"
    assert "будущих объявлениях" not in main
    assert "ошибка бывает больше" in main and "диапазоном" in main
    # «переобучение» в ML — overfitting; пишем по-человечески
    assert "переобуч" not in main.lower() and "обучаем модель заново" in main


def test_about_money_error_is_derived_live_from_mape():
    """«±N млн ₸ для квартиры за 40 млн» считается из той же живой средней ошибки."""
    html = _static("about.html")

    assert "Для квартиры за 40 млн ₸ это около" in html and "data-x40" in html
    assert "h.model_error_pct*0.4" in html and "B.put('[data-x40]'" in html
    assert "4,04" not in html


def test_about_says_who_makes_it_and_why_without_inventing_people():
    main = _main()

    assert "Кто делает и зачем" in main
    assert 'href="https://github.com/Dex719/krisha-fair-price"' in main
    assert 'href="https://t.me/Hopepe1"' in main
    assert "учебный проект" in main  # README, «Дисклеймер»
    # лицензия ELv2 — исходный код доступен, но не «открытый»
    assert "лицензия ELv2" in main
    for wrong in ("весь стек открыт", "Открытый код", "открытый код"):
        assert wrong not in main


def test_about_has_no_unbacked_promises_and_honest_timing():
    main = _main()

    # механизма «исключить лот по просьбе владельца» в коде нет
    assert "по просьбе владельца" not in main
    # «одна секунда» противоречила «до 30 секунд» на холодном старте
    assert "одна секунда" not in main and "до 30 секунд" in main
    # заголовки внутри карточек — h3, а не h2
    assert '<h2 class="prh"' not in main and '<h2 class="limh"' not in main
    # стек — одна строка, без 12 КБ логотипов
    assert 'class="tgrid"' not in main and "Python 3.12" not in main
