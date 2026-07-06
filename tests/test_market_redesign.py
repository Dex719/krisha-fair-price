"""Acceptance checks for issue #80 market page redesign."""

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
    assert '<a class="nl on" href="/stats">Рынок</a>' in html
    assert '<a class="nl" href="/#how">О проекте</a>' in html
    assert "m3.css" not in html
    assert "FairPrice" not in html
    assert "Manrope" not in html
    assert "chart.js" not in html.lower()


def test_market_page_keeps_live_data_endpoints_and_no_mock_numbers():
    html = _static("stats.html")

    for endpoint in ("/api/stats", "/api/heatmap", "/api/forecast"):
        assert endpoint in html
    for fake in (
        "740 тыс",
        "9 364",
        "5 июля 2026",
        "707 до 740",
        "1 052",
        "968 тыс",
        "842 тыс",
        "716 тыс",
        "557 тыс",
        "2 043",
        "1 214",
        "~31 день",
        "+4,6%",
        "+0,7%",
        "обновлено 1 июля 2026",
    ):
        assert fake not in html
    assert "без подстановок" in html
    assert "Источник всех чисел — /api/stats" in html
    assert "Строка района открывает Telegram-бот" in html
    assert "на подписку по району" not in html
    assert "подписку именно на него" not in html
    assert "строка района ведёт в подписку" not in html
    assert "Подпишитесь на свой район" not in html


def test_market_page_has_skeletons_fail_soft_and_v24_chart_rules():
    html = _static("stats.html")
    css = _static("design.css")

    assert "skchart" in html
    assert "chart-empty" in html
    assert "Данные рынка временно недоступны" in html
    assert "Карта временно недоступна" in html
    assert "Прогноз временно недоступен" in html
    assert "grid-line" in html
    assert "ylab" in html
    assert "Шкала Y явно подписана HTML-метками" in html
    assert ".grid-line" in css
    assert ".ylab" in css
    assert ".mrow" in css
    assert ".map-legend" in css
    assert "border:1px dashed var(--rule2)" in css
    assert ".band .chart-empty" in css


def test_market_map_preserves_leaflet_and_required_zoom_controls():
    html = _static("stats.html")

    assert "leaflet@1.9.4" in html
    assert "L.map" in html
    assert "scrollWheelZoom:false" in html
    assert "mouseenter" in html and "map.scrollWheelZoom.enable()" in html
    assert "mouseleave" in html and "map.scrollWheelZoom.disable()" in html
    assert 'data-map-zoom="in"' in html
    assert 'data-map-zoom="out"' in html
    assert "map.zoomIn()" in html and "map.zoomOut()" in html
    assert "touchZoom:true" in html
    assert "basemaps.cartocdn.com" in html


def test_csp_not_weakened_for_market_page():
    assert "default-src 'self'" in CSP
    assert "font-src 'self'" in CSP
    assert "img-src 'self' data: https://*.kcdn.online https://*.basemaps.cartocdn.com" in CSP
    assert "https://cdn.jsdelivr.net" in CSP  # existing Leaflet/legacy allowance, not broadened
    assert "fonts.googleapis.com" not in CSP
    assert "fonts.gstatic.com" not in CSP
