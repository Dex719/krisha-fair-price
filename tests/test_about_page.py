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
    html = _static("about.html")

    for hook in ('data-l="mape"', 'data-l="mdape"', 'data-l="total"', 'data-l="age"'):
        assert hook in html, f"нет живой подстановки {hook}"
    # ширина интервала зависит от лота, поэтому фиксированного числа быть не должно
    assert "±9,5%" not in html
    assert "model_error_pct" in html
    assert "цифры из последнего успешного обновления" in html


def test_about_lists_only_real_bot_features():
    html = _static("about.html")

    assert "Telegram-бот умеет три вещи" in html
    assert "/track" in html and "/alerts" in html
    for promise in ("скоро добавим", "скоро появится", "в разработке"):
        assert promise not in html.lower()


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


def test_about_shows_temporal_validity_caveat():
    """issue #158: голый процент точности читается как обещание.

    Пока `temporal_validity` в мете false, страница обязана сказать вслух, что
    число описывает попадание по текущему стоку, а не будущие объявления.
    """
    html = _static("about.html")

    assert 'data-l="tvnote"' in html
    assert "model_temporal_validity" in html
    assert "а не обещание такой же точности на будущих объявлениях" in html


def test_about_mae_is_live_not_frozen():
    """MAE стоял в разметке руками (4,04 млн ₸) и разъехался с метой на 0.35."""
    html = _static("about.html")

    assert 'data-l="mae"' in html
    assert "model_mae" in html
    assert "4,04" not in html
