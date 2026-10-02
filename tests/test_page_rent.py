"""Контракт страницы «Аренда» (/rent): рынок аренды на общем design.css."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from krisha.api.app import app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static() -> str:
    return (STATIC / "rent.html").read_text(encoding="utf-8")


def _get(path: str, **kwargs):
    with TestClient(app) as client:
        return client.get(path, **kwargs)


def test_rent_route_serves_html_with_title_and_design_css():
    resp = _get("/rent", headers={"accept-encoding": "identity"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    title = re.search(r"<title>(.*?)</title>", resp.text, re.S)
    assert title and "Аренда" in title.group(1)
    assert '<meta name="description"' in resp.text
    assert 'rel="canonical" href="https://dex719-krisha-fair-price.hf.space/rent"' in resp.text
    assert "/static/design.css" in resp.text
    assert "/api/stats/rent" in resp.text


def test_rent_page_has_single_h1_and_logical_headings():
    html = _static()

    assert len(re.findall(r"<h1[\s>]", html)) == 1
    assert html.index("<h1") < html.index("<h2")
    for heading in ("Районы Алматы", "Аренда по комнатам", "Купить, чтобы сдавать?", "Куда дальше"):
        assert heading in html


def test_rent_nav_marks_rent_as_current_page():
    html = _static()

    assert '<a class="on" href="#" aria-current="page">Аренда</a>' in html
    # остальные пункты остаются обычными ссылками
    assert '<a href="/">Оценка</a>' in html
    assert '<a href="/stats">Рынок</a>' in html
    assert '<a href="/about">О проекте</a>' in html
    assert html.count('aria-current="page"') == 1


def test_rent_page_is_self_hosted_without_external_fonts_or_cdns():
    html = _static()

    low = html.lower()
    for banned in (
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "cdnjs.cloudflare.com",
        "cdn.jsdelivr.net",
        "unpkg.com",
        "chart.js",
        "m3.css",
    ):
        assert banned not in low, banned
    assert "FairPrice" not in html
    assert "/static/fonts/onest-cyr.woff2" in html
    assert "/static/fonts/unb-cyr.woff2" in html


def test_sitemap_lists_rent_page():
    resp = _get("/sitemap.xml")

    assert resp.status_code == 200
    assert "/rent</loc>" in resp.text


def test_rent_numbers_come_from_api_not_from_markup():
    """Цифр рынка в разметке нет: KPI, районы, гистограмма, динамика рисуются из /api/stats/rent."""
    html = _static()

    for field in (
        "total_listings",
        "median_rent",
        "median_ppsm",
        "by_district",
        "rent_hist",
        "by_rooms",
        "trend",
        "gross_yield_pct",
        "updated_at",
    ):
        assert field in html, f"страница не читает поле {field}"
    for fn in ("drawKpi", "drawDistricts", "drawRooms", "drawHist", "drawTrend"):
        assert f"function {fn}(" in html
    # KPI до ответа API — скелетоны с прочерком, а не выдуманные числа
    kpi = html[html.index('class="knums"'):html.index('id="districts"')]
    assert kpi.count('class="ph sk"') == 3
    assert not re.search(r"\d[\d  ]{3,}\d", kpi), "в KPI-полосе не должно быть захардкоженных чисел"


def test_rent_page_degrades_calmly_when_api_is_down():
    html = _static()

    assert "Данные аренды временно недоступны" in html
    assert "function fail(" in html
    assert 'data-r="err"' in html and "hidden" in html
    # 503 и сетевая ошибка ведут в одну и ту же ветку
    assert "if (!r.ok) throw" in html
    assert ".then(apply, function(){ fail(); })" in html


def test_rent_trend_needs_at_least_three_weeks():
    html = _static()

    assert "tr.length < 3" in html
    assert 'id="trend"' in html and 'data-r="trendsec" hidden' in html


def test_rent_charts_stay_interactive_after_redraw():
    """Столбики и точки перерисовываются живыми данными, поэтому клик делегирован документу."""
    html = _static()

    assert "e.target.closest('.' + cls)" in html
    assert "tapToggle('hcol', e)" in html and "tapToggle('cband', e)" in html
    assert "querySelectorAll('.hcol').forEach(c=>{" not in html
    # золото — выше медианы, лайм — ниже
    assert ".hcol.hi .hbar{background:var(--vio)}" in html
    assert ".dfl.hi{background:var(--vio)}" in html and ".dfl.lo{background:var(--lime)}" in html


def test_rent_yield_explainer_matches_api_formula():
    html = _static()

    assert "Купить, чтобы сдавать?" in html
    assert "<b>12</b> × аренда в месяц" in html and "цена покупки" in html
    assert "Если сдавать эту квартиру" in html
    for omitted in ("Налогов", "простоя", "ремонта", "коммунальных"):
        assert omitted in html


def test_rent_cta_form_jumps_to_home_check_and_next_cards_link_site():
    html = _static()

    assert '<form class="inbar" data-jump' in html
    assert "location.href='/#check=' + m[1]" in html
    nxt = html[html.index('class="ncards"'):]
    for href in ('href="/"', 'href="/stats"', 'href="/bot"', 'href="/about"'):
        assert href in nxt, href


def test_rent_page_colors_come_from_theme_tokens():
    """Цвета берутся из токенов темы: в стилях страницы нет захардкоженных hex."""
    html = _static()
    page_css = html[html.index("/* ---- оболочка подстраницы ---- */"):html.index("</style><link rel=\"icon\"")]

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", page_css)
    assert "prefers-reduced-motion" in html and "html.lite" in html


def test_rent_failure_state_reveals_empty_panels():
    """При сбое API панели с [data-reveal] не остаются прозрачными: «Нет данных» видно и при html.anim."""
    html = _static()
    start = html.index("function fail(")
    fail_body = html[start:html.index("function apply(", start)]

    assert "reveal();" in fail_body
    assert "#prices .shmeta" in fail_body


def test_rent_mobile_menu_marks_current_page_by_path():
    html = _static()

    assert "location.pathname" in html
    assert "location.href.split('#')[0]===a.dataset.href" not in html
    assert "HOME_URL" not in html


def test_rent_room_mix_colors_stay_visible_in_both_themes():
    """Оттенки структуры предложения идут от токенов темы, а не от одного смешивания с панелью."""
    html = _static()

    assert "var(--mc' + Math.min(i, 4) + ')" in html
    assert re.search(r"html\[data-theme=light\] \.two\{--mc1:color-mix\(in oklab,var\(--lime\) \d+%,var\(--ink\)\)", html)
    assert "color-mix(in oklab,var(--lime) ' + mix" not in html


def test_rent_districts_table_header_matches_row_cells():
    html = _static()
    head = html[html.index('class="dhd"'):html.index('data-r="districts"')]

    assert 'role="presentation"' not in head
    assert head.count('role="columnheader"') == 5 + 1  # номер, район, метр (на 2 колонки), квартира, объявлений, доходность
    assert 'aria-colspan="2"' in head
