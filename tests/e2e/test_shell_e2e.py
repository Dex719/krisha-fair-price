"""Браузерные e2e общего каркаса (static/js/site.js): меню, живые цифры, GSAP по страницам,
«меньше движения», формы-переходы и аккордеон без GSAP. Герметично, см. conftest.py."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

SUBPAGES = ("/about", "/bot", "/privacy", "/terms")


def _ready(page):
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")
    page.wait_for_function("() => !document.querySelector('.sk')")


def test_live_numbers_agree_everywhere_and_count_up_ends_on_fresh_value(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/")
    _ready(page)
    page.locator(".mets").scroll_into_view_if_needed()
    page.wait_for_timeout(1800)
    for key in ("total", "mape"):
        texts = set(page.locator(f"[data-l={key}]").all_text_contents())
        assert len(texts) == 1, f"[data-l={key}] расходятся: {texts}"
    assert set(page.locator("[data-l=total]").all_text_contents()) == {"18\u00a0680"}
    # \u0441\u043b\u043e\u0432\u043e \u043f\u0440\u0438 \u0447\u0438\u0441\u043b\u0435 \u0441\u043a\u043b\u043e\u043d\u044f\u0435\u0442 bagam.plural: \u00ab18 680 \u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u0439\u00bb, \u00ab\u0441\u0440\u0430\u0432\u043d\u0438\u043c \u0441 18 680 \u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u044f\u043c\u0438\u00bb
    assert page.locator("footer [data-l=totalw]").text_content() == "\u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u0439"
    assert page.locator(".hnote [data-l=totalw]").text_content() == "\u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u044f\u043c\u0438"
    expect(page.locator("footer .flive").first).to_be_visible()


@pytest.mark.parametrize("path,current", [("/", "/"), ("/stats", "/stats"), ("/bot", "/bot"), ("/privacy", None)])
def test_mobile_menu_is_a_dialog_with_focus_trap(hermetic_page, mock_api, hermetic_server, path, current):
    page = hermetic_page
    page.set_viewport_size({"width": 390, "height": 844})
    mock_api()
    page.goto(hermetic_server + path)
    burg, menu = page.locator(".burg"), page.locator("#mmenu")

    burg.click()
    expect(menu).to_have_class(re.compile(r"\bopen\b"))
    expect(menu).to_have_attribute("role", "dialog")
    expect(menu).to_have_attribute("aria-modal", "true")
    expect(burg).to_have_attribute("aria-expanded", "true")
    cur = menu.locator('a.ml[aria-current="page"]')
    if current:
        expect(cur).to_have_attribute("href", current)
        expect(cur).to_be_focused()
    else:
        expect(cur).to_have_count(0)
    for _ in range(12):
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.getElementById('mmenu').contains(document.activeElement)"
                             " || document.activeElement === document.querySelector('.burg')")
    page.keyboard.press("Escape")
    expect(menu).not_to_have_class(re.compile(r"\bopen\b"))
    expect(burg).to_be_focused()


@pytest.mark.parametrize("path", SUBPAGES)
def test_subpages_do_not_load_gsap_and_show_everything(hermetic_page, mock_api, hermetic_server, path):
    page = hermetic_page
    mock_api()
    loaded = []
    page.on("request", lambda r: loaded.append(r.url) if "gsap" in r.url.lower() else None)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(hermetic_server + path, wait_until="load")
    _ready(page)
    page.wait_for_timeout(1500)
    assert loaded == [], f"{path}: GSAP не нужен на странице без анимаций"
    # заголовок не прячется и не «влетает» повторно
    assert page.evaluate("getComputedStyle(document.querySelector('h1')).opacity") == "1"
    assert errors == []


def test_reduced_motion_skips_gsap_and_counts(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    page.emulate_media(reduced_motion="reduce")
    mock_api()
    loaded = []
    page.on("request", lambda r: loaded.append(r.url) if "gsap" in r.url.lower() else None)
    page.goto(hermetic_server + "/", wait_until="load")
    _ready(page)
    page.wait_for_timeout(1500)
    assert loaded == []
    assert page.evaluate("document.querySelectorAll('[data-rv]').length") == 0
    # \u0441\u0447\u0451\u0442\u0447\u0438\u043a \u043d\u0430 \u0433\u043b\u0430\u0432\u043d\u043e\u0439 (\u0442\u043e\u0447\u043d\u043e\u0441\u0442\u044c \u043c\u043e\u0434\u0435\u043b\u0438) \u0441\u0440\u0430\u0437\u0443 \u043f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442 \u0438\u0442\u043e\u0433, \u0431\u0435\u0437 \u0431\u0435\u0433\u0430 \u043e\u0442 \u043d\u0443\u043b\u044f
    assert set(page.locator(".met [data-l=mape]").all_text_contents()) == {"7,6%"}


def test_jump_form_and_faq_work_before_any_animation_library(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.route("**/static/js/gsap.min.js*", lambda r: r.abort())
    page.goto(hermetic_server + "/about", wait_until="domcontentloaded")

    page.locator(".faq .q").first.click()
    expect(page.locator(".faq .qa").first).to_have_class(re.compile(r"\bopen\b"))
    expect(page.locator(".faq .q").first).to_have_attribute("aria-expanded", "true")

    form = page.locator("form[data-jump]")
    form.locator("input").fill("https://krisha.kz/a/show/761891663")
    form.locator("input").press("Enter")
    page.wait_for_url(re.compile(r".*/#check=761891663$"))


def test_theme_toggle_updates_theme_color(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/terms")
    page.click(".thbtn")
    theme = page.get_attribute("html", "data-theme")
    want = "#F1F4EC" if theme == "light" else "#0A100C"
    assert set(page.locator('meta[name="theme-color"]').evaluate_all("els => els.map(e => e.content)")) == {want}
    assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == theme
