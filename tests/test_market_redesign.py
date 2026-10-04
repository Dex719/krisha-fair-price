"""Контракт страницы «Рынок» (/stats): продажа и аренда на одной странице.

«Аренда» (бывший /rent) — режим этой же страницы: ?mode=rent, переключатель «Продажа | Аренда».
Все цифры рынка приходят из /api/stats и /api/stats/rent, в разметке их нет.
"""

import re
from pathlib import Path

from krisha.api.app import CSP

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str = "stats.html") -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _markup(html: str) -> str:
    """Разметка без скриптов и стилей — то, что видно до ответа API."""
    html = re.sub(r"<script\b.*?</script>", "", html, flags=re.S)
    return re.sub(r"<style\b.*?</style>", "", html, flags=re.S)


def _page_script(html: str) -> str:
    start = html.index("/* «Рынок»: продажа и аренда на одной странице.")
    return html[start:html.index("</script>", start)]


def _page_css(html: str) -> str:
    start = html.index("/* ---- «Рынок»: продажа и аренда на одной странице ----")
    return html[start:html.index("</style>", start)]


def test_market_page_uses_bagam_chrome_meta_and_design_css():
    html = _static()

    assert "<title>Цены на квартиры в Алматы — продажа и аренда по районам │ baǵam</title>" in html
    assert '<meta name="description"' in html
    assert 'href="/static/design.css"' in html
    assert 'rel="icon" type="image/svg+xml" href="/static/favicon.svg"' in html
    assert '<link rel="canonical" href="https://bagam.info/stats">' in html
    assert '<a href="/">Оценка</a>' in html
    assert '<a href="/about">О проекте</a>' in html
    for banned in ("m3.css", "FairPrice", "chart.js", "fonts.gstatic.com", "fonts.googleapis.com",
                   "cdnjs.cloudflare.com", "unpkg.com"):
        assert banned not in html, banned


def test_rent_page_is_merged_into_market():
    """Отдельной страницы аренды больше нет: /rent — 301 на /stats?mode=rent (см. test_page_rent)."""
    assert not (STATIC / "rent.html").exists()
    html = _static()
    assert not re.search(r'href="/rent[#"?]', html)


def test_mode_switch_is_one_page_with_url_state():
    html = _static()
    head = html[:html.index("</head>")]
    script = _page_script(html)

    # режим выставляется до первой отрисовки — без вспышки чужих подписей
    assert "setAttribute('data-mode'" in head and "mode=rent" in head
    assert "html:not([data-mode=rent]) [data-show=rent],html[data-mode=rent] [data-show=sale]{display:none!important}" in html
    # сегментированный переключатель: две кнопки с aria-pressed
    switch = re.search(r'<div class="mswitch"[^>]*>(.*?)</div>', html, re.S).group(1)
    assert switch.count("<button") == 2 and switch.count("aria-pressed") == 2
    assert 'data-set="sale"' in switch and 'data-set="rent"' in switch
    # адрес меняется без новой записи в истории, подсветка в шапке и подвале пересчитывается
    assert "history.replaceState(" in script and "searchParams.set('mode', 'rent')" in script
    assert "searchParams.delete('mode')" in script
    assert "B.markCurrent()" in script
    # одна форма проверки на оба режима — переход на главную делает site.js
    assert html.count("<form") == 1 and '<form class="inbar" data-jump' in html
    assert "о продаже или об аренде" in html


def test_numbers_come_from_api_not_from_markup():
    """Никаких вшитых снимков: до ответа API — скелетоны, а не цифры прошлой сборки."""
    html = _static()
    script = _page_script(html)
    markup = _markup(html)

    assert "fetch('/api/stats/rent'" in script
    assert "document.addEventListener('stats'" in script and "B.live.then(" in script
    for field in ("total_listings", "median_price", "median_ppsm", "median_rent", "by_district",
                  "price_hist", "rent_hist", "by_rooms", "trend", "gross_yield_pct", "updated_at"):
        assert field in script, f"страница не читает поле {field}"
    for fn in ("drawKpi", "drawTrend", "drawDistricts", "drawHist", "drawRooms", "drawYield"):
        assert f"function {fn}(" in script
    main = markup[markup.index('<main id="main"'):markup.index("</main>")]
    # ни одного числа рынка в разметке: районов, медиан, недель
    for leftover in ("Медеуский", "Алатауский", "741 935", "21 113", "08.06", "лотов"):
        assert leftover not in main, leftover
    kpi = main[main.index('class="knums"'):main.index('id="trend"')]
    assert kpi.count('class="sk"') == 3
    assert not re.search(r"\d[\d  ]{3,}\d", kpi)
    # «Без подстановок…» обещало честность при сбое, а показывало старый снимок — фразы нет
    assert "Без подстановок" not in html


def test_api_failure_shows_calm_error_instead_of_old_numbers():
    html = _static()
    script = _page_script(html)

    assert 'data-r="err" hidden' in html
    assert "Данные о продаже временно недоступны" in script and "Данные об аренде временно недоступны" in script
    # 503 и сетевая ошибка аренды — одна ветка; продажа — только свежий ответ, не кэш браузера
    assert "if (!r.ok) throw" in script
    assert "st.sale = ok(fresh) ? fresh : null" in script
    # при сбое панели с цифрами скрыты целиком, остаётся плашка
    assert "#main[data-state=error] .knums,#main[data-state=error] .dsec" in html
    assert "main.setAttribute('data-state', 'error')" in script
    # страховка от вечного скелетона
    assert "15000" in script


def test_data_date_is_honest_about_stale_data():
    script = _page_script(_static())

    assert "'данные на '" in script and "'обновлено '" in script
    assert "/ 36e5 > 72" in script
    assert "freshness === 'stale'" in script
    assert "Алматы, UTC+5" in script


def test_week_chart_tooltips_share_coordinates_and_one_tab_stop():
    """Точки, подсказка, подписи и столбики выборки считаются из одних xs; график — одна остановка Tab."""
    html = _static()
    script = _page_script(html)

    plot = re.search(r'<div class="cplot[^"]*"[^>]*>', html).group(0)
    assert 'tabindex="0"' in plot and 'role="group"' in plot
    for key in ("ArrowLeft", "ArrowRight", "Home", "End", "Escape"):
        assert f"'{key}'" in script
    assert 'aria-live="polite"' in html and 'class="vh" data-r="ttab"' in html
    # ни кнопки на неделю, ни кнопки на столбик гистограммы
    assert 'class="cband"' not in html and "class=\"hcol" not in html and "cband" not in script
    # одна формула x на всё: (позиция + .5) / span
    assert "+ .5) / span * 100" in script
    assert "tr.length < 3" in script
    # подписи недель и количество — из данных, а не «десять недель»
    assert "десять недель" not in html
    assert "plural(n, ['неделя', 'недели', 'недель'])" in script


def test_rent_numbers_are_per_flat_per_month():
    script = _page_script(_static())

    # главные цифры аренды — за квартиру в месяц: однушка и двушка из by_rooms
    assert "'1-комнатная'" in script and "'2-комнатная'" in script
    assert "₸/м² в мес" not in script
    # подписи вилок по-человечески: «до 100 тыс», «700 тыс–1 млн», «от 1 млн»
    assert "'от '" in script and "'до '" in script and "'млн'" in script
    # тренд аренды — в процентах к первой неделе
    assert "p.median_ppsm / base - 1" in script


def test_districts_table_is_shared_with_yield():
    html = _static()
    head = html[html.index('class="dhd"'):html.index('data-r="districts"')]

    assert 'data-meta="districts"' in html and 'class="drows"' in html
    assert head.count('role="columnheader"') == 7
    for col in ("Район", "Цена м²", "Аренда в месяц", "Доходность в год", "Объявлений"):
        assert col in head
    script = _page_script(html)
    # доходность без ложной точности; отклонение от города — числом со знаком, не только цветом
    assert "'≈' + Math.round(y) + '%'" in script
    assert "signed((v - city) / city * 100)" in script
    # шапка на узком экране не исчезает для скринридера, в строке появляются подписи
    css = _page_css(html)
    assert ".drows .dhd{position:absolute;width:1px;height:1px" in css
    assert ".dhd{display:none}" not in css


def test_texts_and_plurals():
    html = _static()
    script = _page_script(html)

    for gone in ("Медиана метра", "Медианная цена по комнатности", "Вторичка и новостройки",
                 "Что сдают чаще", "Ошибка аренды", "лайм", "Лайм", "золото", "Золото", "единичны"):
        assert gone not in html, gone
    # подвал общий для всех страниц (test_site_shell) — проверяем содержимое страницы
    own = html[html.index('<main id="main">'):html.index("</main>")] + script
    assert not re.search(r"\bлот(а|ов)?\b", own)
    assert "Типичная цена м²" in html and "Цена по числу комнат" in html
    assert "plural(" in script and "B.ru" in script
    assert "['объявление', 'объявления', 'объявлений']" in script
    # мало объявлений — помечено, а слишком мало — названо цифрой из данных
    assert "MIN_ROOMS = 30" in script and "слишком мало для типичной цены" in script


def test_no_gsap_and_no_dead_code():
    html = _static()

    assert "data-gsap" not in html and "gsap." not in html and "ScrollTrigger" not in html
    for dead in (".herorow", ".cityband", ".glow", ".lede", "h1 span", 'id="cg"', "waitmsg", ".sheet.busy",
                 "data-l=\"ppsm\"", "data-l=\"medprice\"", "data-l=\"tvnote\"", "data-skel"):
        assert dead not in html, dead
    assert html.rstrip().endswith("</body></html>")
    assert html.count("</html>") == 1
    assert "B.reveal(" in _page_script(html) and "B.countUp(" in _page_script(html)


def test_page_colors_come_from_theme_tokens():
    html = _static()
    css = _page_css(html)

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    # светлая тема: заливки графиков темнее, чтобы держать 3:1 к дорожке и панели
    assert "html[data-theme=light]{--c-lo:" in css
    assert "prefers-reduced-motion" in (STATIC / "design.css").read_text(encoding="utf-8")


def test_single_h1_and_logical_headings():
    html = _static()

    assert len(re.findall(r"<h1[\s>]", html)) == 1
    assert html.index("<h1") < html.index("<h2")
    for heading in ("Районы Алматы", "Цена по числу комнат", "Купить, чтобы сдавать?"):
        assert heading in html


def test_yield_explainer_matches_api_formula():
    html = _static()

    assert "<b>12</b> × аренда в месяц" in html and "цена покупки" in html
    assert "Если сдавать эту квартиру" in html
    for omitted in ("Налогов", "простоя", "ремонта", "коммунальных"):
        assert omitted in html


def test_no_next_cards_cta_is_last():
    """«Куда дальше» дублировал шапку и подвал (как на главной) — страница кончается формой проверки."""
    html = _static()
    main = html[html.index('<main id="main"'):html.index("</main>")]

    assert "Куда дальше" not in html and 'class="ncards"' not in html and 'class="next"' not in html
    cta = main[main.rindex("<section"):]
    assert cta.startswith('<section class="cta"') and 'class="inbar" data-jump' in cta


def test_market_page_has_no_removed_map_leftovers():
    """Карту сняли по решению продукта — на странице не должно остаться её следов."""
    html = _static()

    for leftover in ("leaflet", "L.map", "basemaps.cartocdn.com", "map-legend"):
        assert leftover not in html, f"остался хвост карты: {leftover}"


def test_csp_not_weakened_for_market_page():
    assert "default-src 'self'" in CSP
    assert "font-src 'self'" in CSP
    assert "fonts.googleapis.com" not in CSP
    assert "fonts.gstatic.com" not in CSP
