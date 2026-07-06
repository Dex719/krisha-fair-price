"""Acceptance checks for issue #81 about page."""

from pathlib import Path

from fastapi.testclient import TestClient

from krisha.api.app import CSP, app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


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
    html = _static("about.html")

    assert "Как мы считаем справедливую цену квартиры │ baǵam" in html
    assert '<meta name="description"' in html
    assert 'href="/static/design.css"' in html
    assert 'rel="icon" type="image/svg+xml" href="/static/favicon.svg"' in html
    assert '<a class="nl on" href="/about">О проекте</a>' in html
    assert '<a class="nl" href="/stats">Рынок</a>' in html
    assert "/api/stats" in html
    assert "/api/health" in html
    assert "model_error_pct" in html
    assert "m3.css" not in html
    assert "FairPrice" not in html
    assert "Manrope" not in html


def test_about_page_has_no_mock_numbers_for_live_metrics():
    html = _static("about.html")

    for fake in (
        "11 357",
        "9 364",
        "±9,5%",
        "±9.5%",
        "23 факторам",
        "данные обновляются каждое утро",
        "без числа из макета",
        "устаревшие значения макета",
        "`/api/stats`",
        "`/api/health`",
        "health недоступен",
        "Все числа в этом блоке пришли из API",
    ):
        assert fake not in html
    assert "Считается по активным объявлениям базы" in html
    assert "Медианная ошибка последней опубликованной модели" in html
    assert "Живые значения появятся через пару секунд" in html
    assert "данные о модели временно недоступны" in html


def test_about_faq_matches_mockup_with_live_trust_metric():
    html = _static("about.html")

    assert "Откуда данные и законно ли это?" in html
    assert "Мы собираем только открытые объявления" in html
    assert "Почему бесплатно?" in html
    assert "Это независимый проект. Если когда-нибудь появятся платные функции" in html
    assert "Насколько можно доверять оценке?" in html
    assert "about-trust-answer" in html
    assert "На проверочной выборке медианная ошибка — '+err" in html
    assert "Данные о точности модели временно недоступны" in html
    assert "Почему результат может отличаться от цены сделки?" not in html
    assert "Что делать после оценки?" not in html


def test_about_css_and_nav_are_connected():
    css = _static("design.css")
    index = _static("index.html")
    market = _static("stats.html")

    assert ".prose" in css
    assert ".about-live" in css
    assert ".about-band" in css
    assert '<a class="nl" href="/about">О проекте</a>' in index
    assert '<a class="nl" href="/about">О проекте</a>' in market


def test_health_exposes_model_error_pct_without_csp_changes():
    client = TestClient(app)
    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert "model_error_pct" in resp.json()
    assert "fonts.googleapis.com" not in CSP
    assert "fonts.gstatic.com" not in CSP
