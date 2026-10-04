"""Общий каркас всех страниц: <head>, шапка, мобильное меню, подвал, static/js/site.js.

Каркас один на все страницы и различается только aria-current. Если правка одной
страницы разведёт шапку или подвал с остальными — тест покажет, где именно.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
PAGES = sorted(p.name for p in STATIC.glob("*.html"))
SITE_JS = (STATIC / "js" / "site.js").read_text(encoding="utf-8")
CSS = (STATIC / "design.css").read_text(encoding="utf-8")


def _page(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _block(html: str, start: str, end: str) -> str:
    i = html.index(start)
    return html[i:html.index(end, i) + len(end)]


def _shell(html: str) -> dict[str, str]:
    """Шапка, меню и подвал без aria-current — то, что обязано совпадать на всех страницах."""
    parts = {
        "nav": _block(html, "<nav", "</nav>"),
        "mmenu": _block(html, '<div class="mmenu"', '<main id="main">'),
        "footer": _block(html, "<footer>", "</footer>"),
    }
    return {k: v.replace(' aria-current="page"', "") for k, v in parts.items()}


@pytest.mark.parametrize("name", PAGES)
def test_head_is_shared(name):
    html = _page(name)
    head = html[:html.index("</head>")]

    assert '<script src="/static/js/site.js" defer' in head
    assert '<link rel="stylesheet" href="/static/design.css">' in head
    # тема до первой отрисовки: сохранённый выбор, иначе системная — без вспышки
    assert "localStorage.getItem('bagam-theme')" in head and "prefers-color-scheme: light" in head
    assert head.count('<meta name="theme-color"') == 2
    assert '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">' in head
    # шрифты объявлены один раз в design.css, на странице — только preload логотипа и текста
    assert "@font-face" not in html
    for font in ("unb-lat", "unb-gacute", "onest-cyr"):
        assert f'href="/static/fonts/{font}.woff2"' in head
    if 'name="robots" content="noindex"' not in head:
        for prop in ("og:site_name", "og:image:width", "og:image:height", "og:image:alt"):
            assert f'property="{prop}"' in head, f"{name}: нет {prop}"
        assert 'content="https://bagam.info/static/img/og.jpg"' in head


@pytest.mark.parametrize("name", PAGES)
def test_no_copies_of_shared_code(name):
    html = _page(name)

    for leftover in ("bagamBoot", "bagamTG", "HOME_URL", "no-store", "@keyframes bgmsk",
                     ".offbar{", "html.lite *{", "/* ---------- тема ---------- */"):
        assert leftover not in html, f"{name}: копия общего кода «{leftover}»"
    # /rent — режим «Рынка»
    assert not re.search(r'href="/rent[#"]', html), f"{name}: ссылка на /rent"
    # GSAP — только там, где есть что анимировать. «Рынку» хватает bagam.reveal/countUp и CSS
    assert ("data-gsap" in html) == (name == "index.html"), name


def test_shell_markup_is_identical_on_every_page():
    reference = _shell(_page("index.html"))
    for name in PAGES:
        shell = _shell(_page(name))
        for part, markup in reference.items():
            assert shell[part] == markup, f"{name}: {part} отличается от главной"


def test_nav_and_mobile_menu_contract():
    nav = _shell(_page("index.html"))["nav"]
    mmenu = _shell(_page("index.html"))["mmenu"]

    items = re.findall(r'<a href="([^"]+)">([^<]+)</a>', re.search(r'<div class="menu">(.*?)</div>', nav).group(1))
    assert items == [("/", "Оценка"), ("/stats", "Рынок"), ("/bot", "Telegram-бот"), ("/about", "О проекте")]
    assert '<a class="tgbtn" href="https://t.me/fairprice_kzbot"' in nav and "Открыть бота" in nav
    assert 'role="dialog"' in mmenu and 'aria-modal="true"' in mmenu
    assert re.findall(r'<a class="ml" href="([^"]+)"', mmenu) == ["/", "/stats", "/bot", "/about"]


def test_footer_numbers_are_live_placeholders():
    footer = _shell(_page("index.html"))["footer"]

    # цифры точности — только через [data-l], до ответа API — «—», а не устаревшая вшитая цифра
    for key in ("total", "mape", "rmape"):
        assert re.findall(rf'data-l="{key}">([^<]*)<', footer) and set(re.findall(rf'data-l="{key}">([^<]*)<', footer)) == {"—"}
    assert 'href="/stats?mode=rent"' in footer and 'href="#"' not in footer


@pytest.mark.parametrize("name,current", [
    ("index.html", ('href="/"', 'href="/"')),
    ("stats.html", ('href="/stats"', 'href="/stats"')),
    ("about.html", ('href="/about"', 'href="/about"')),
    ("bot.html", ('href="/bot"', 'href="/bot"')),
    ("privacy.html", (None, 'href="/privacy"')),
    ("terms.html", (None, 'href="/terms"')),
    ("404.html", (None, None)),
])
def test_current_page_is_marked_with_aria_current(name, current):
    html = _page(name)
    nav = _block(html, '<div class="menu">', "</div>")
    footer = _block(html, "<footer>", "</footer>")
    nav_cur, foot_cur = current

    assert nav.count('aria-current="page"') == (1 if nav_cur else 0)
    if nav_cur:
        assert f'{nav_cur} aria-current="page"' in nav
    assert footer.count('aria-current="page"') == (1 if foot_cur else 0)
    if foot_cur:
        assert f'{foot_cur} aria-current="page"' in footer


def test_site_js_api_and_fixes():
    for api in ("B.ready", "B.boot", "B.put", "B.live", "B.ru", "B.fmt", "B.pct", "B.plural",
                "B.glide", "B.reveal", "B.countUp", "B.markCurrent", "B.tg", "B.setTheme"):
        assert api in SITE_JS, f"site.js: нет {api}"
    # «Назад» в Telegram ведёт на главную, а не в необъявленный HOME_URL
    assert "location.href = '/'" in SITE_JS and "HOME_URL" not in SITE_JS
    # текущий пункт меню — по пути, а не по полному адресу
    assert "location.pathname" in SITE_JS and "location.href.split" not in SITE_JS
    # счётчик берёт финальное значение в конце, а не до ответа API
    assert "el.textContent = el.__final" in SITE_JS
    # живые цифры кэширует браузер по max-age сервера
    assert "no-store" not in SITE_JS
    # один ключ темы; старый переносится и удаляется
    assert "removeItem(OLD_KEY)" in SITE_JS


def test_design_css_reduced_motion_and_color_scheme():
    assert "@media (prefers-reduced-motion:reduce)" in CSS
    assert "html[data-theme=light]{color-scheme:light}" in CSS
    assert "html[data-theme=dark]{color-scheme:dark}" in CSS
    for dead in (".ridge{", ".map i{", ".msg.alert", ".icf{", ".sheet.busy{opacity"):
        assert dead not in CSS, f"мёртвый селектор {dead}"
    assert CSS.count(".fh{") == 1 and CSS.count("@keyframes bgmsk") == 1


def test_footer_copy_is_honest_and_human():
    footer = _shell(_page("index.html"))["footer"]

    # ответ — обычно несколько секунд, после простоя до 30 с: «за секунду» было неправдой
    assert "за секунду" not in footer and "обычно за несколько секунд, бесплатно" in footer
    # число объявлений: слово склоняет site.js (bagam.plural), между ними неразрывный пробел
    assert '<b data-l="total">—</b>&nbsp;<span data-l="totalw">объявлений</span>' in footer
    assert "лотов" not in footer
    # ошибка модели в подвале — один раз (продажа и аренда в одной строке)
    assert footer.count('data-l="mape"') == 1 and footer.count('data-l="rmape"') == 1
    assert "put('total'" in SITE_JS and 'data-l="totalw"' in SITE_JS and "data-forms" in SITE_JS


def test_source_code_is_not_called_open_source():
    """Лицензия ELv2 — исходный код доступен, но это не open source."""
    mmenu = _shell(_page("index.html"))["mmenu"]

    assert "Исходный код на GitHub" in mmenu
    for name in PAGES:
        low = _page(name).lower()
        for wrong in ("открытый код", "открытым кодом", "весь стек открыт", "open source"):
            assert wrong not in low, f"{name}: «{wrong}»"


def test_404_page_does_not_ask_for_live_numbers():
    """404 не ходит в /api/stats и /api/health: <html data-live="off">, живые цифры подвала спрятаны."""
    for name in PAGES:
        html_tag = re.search(r"<html[^>]*>", _page(name)).group(0)
        assert ('data-live="off"' in html_tag) == (name == "404.html"), name
    assert "root.getAttribute('data-live') !== 'off'" in SITE_JS
    assert "Promise.resolve({ stats: null, health: null })" in SITE_JS
    assert "html[data-live=off] .flive{display:none}" in CSS
    # всё, что в подвале показывает цифры из API, — внутри .flive (кроме подписи свежести с текстом по умолчанию)
    footer = _shell(_page("404.html"))["footer"]
    rest = re.sub(r'<span class="fchip flive">.*?</span></span>', "", footer, flags=re.S)
    rest = re.sub(r'<span class="flive">.*?\.</span>', "", rest, flags=re.S)
    for key in ("total", "totalw", "mape", "rmape"):
        assert f'data-l="{key}"' in footer and f'data-l="{key}"' not in rest, key


def test_no_monospace_or_dead_leftovers():
    """JetBrains Mono удалён: ни псевдонима 'Data', ни DataFB; .nidx и .waitmsg больше нет."""
    for name in PAGES:
        html = _page(name)
        for dead in ("'Data'", "JetBrains", "DataFB", "monospace", 'class="nidx"', "waitmsg"):
            assert dead not in html, f"{name}: {dead}"
    for dead in ("'Data'", "DataFB", "JetBrains", ".nidx", ".waitmsg"):
        assert dead not in CSS, dead
    for dead in ("waitmsg", "MutationObserver"):
        assert dead not in SITE_JS, dead
    for gone in ("avatar.png", "img/city-860.webp", "fonts/jbm-lat.woff2", "rent.html"):
        assert not (STATIC / gone).exists(), gone
        assert all(f"/static/{gone}" not in _page(n) for n in PAGES), gone
