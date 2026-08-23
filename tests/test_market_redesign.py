"""Контракт страницы «Рынок» после редизайна baǵam."""

from pathlib import Path

from krisha.api.app import CSP

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_market_page_uses_bagam_chrome_meta_and_design_css():
    html = _static("stats.html")

    assert "Цены на квартиры в Алматы сегодня — районы, динамика, медианы │ baǵam" in html
    assert '<meta name="description"' in html
    assert 'href="/static/design.css"' in html
    assert 'rel="icon" type="image/svg+xml" href="/static/favicon.svg"' in html
    assert '<a href="/">Оценка</a>' in html
    assert '<a href="/about">О проекте</a>' in html
    assert "m3.css" not in html
    assert "FairPrice" not in html
    assert "chart.js" not in html.lower()
    assert "fonts.gstatic.com" not in html


def test_market_numbers_come_from_api_and_are_redrawn_live():
    """Разметка приходит со снимком сборки, но живой ответ её перерисовывает.

    Без этого страница месяцами показывала бы медианы того дня, когда её собрали,
    и подпись «обновлено» врала бы.
    """
    html = _static("stats.html")

    assert "/api/stats" in html and "/api/health" in html
    for field in ("by_district", "price_hist", "by_rooms", "by_category", "trend"):
        assert field in html, f"нет перерисовки по полю {field}"
    assert "drawTrend" in html and "drawDistricts" in html
    assert "drawHist" in html and "drawRooms" in html
    assert "document.addEventListener('stats'" in html
    assert "Без подстановок" in html
    # если источник молчит — честная подпись, а не свежая дата
    assert "цифры из последнего успешного обновления" in html


def test_market_charts_stay_interactive_after_redraw():
    """Обработчики делегированы документу: перерисованные столбики тоже кликаются."""
    html = _static("stats.html")

    assert "e.target.closest('.hcol')" in html
    assert "e.target.closest('.cband')" in html
    assert "querySelectorAll('.hcol').forEach(c=>{" not in html
    assert "querySelectorAll('.cband').forEach(b=>{" not in html


def test_market_page_has_no_removed_map_leftovers():
    """Карту сняли по решению продукта — на странице не должно остаться её следов."""
    html = _static("stats.html")

    for leftover in ("leaflet", "L.map", "basemaps.cartocdn.com", "map-legend"):
        assert leftover not in html, f"остался хвост карты: {leftover}"


def test_csp_not_weakened_for_market_page():
    assert "default-src 'self'" in CSP
    assert "font-src 'self'" in CSP
    assert "fonts.googleapis.com" not in CSP
    assert "fonts.gstatic.com" not in CSP
