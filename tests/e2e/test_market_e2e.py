"""Браузерные e2e страницы «Рынок» (/stats): продажа и аренда на одной странице.

Герметично (см. conftest.py): /api/stats, /api/health — из mock_api, /api/stats/rent — из
fixtures/stats_rent.json. В разметке страницы цифр нет: всё, что проверяется, нарисовано из API.
"""

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

RENT = json.loads((Path(__file__).parent / "fixtures" / "stats_rent.json").read_text(encoding="utf-8"))
NB = " "


def _ru(n: float) -> str:
    return f"{round(n):,}".replace(",", NB)


@pytest.fixture
def market(hermetic_page, mock_api):
    """mock_api + ответ /api/stats/rent; возвращает страницу."""
    mock_api()
    hermetic_page.route("**/api/stats/rent", lambda r: r.fulfill(
        status=200, body=json.dumps(RENT), content_type="application/json"))
    return hermetic_page


def _ready(page):
    page.wait_for_function("() => !document.querySelector('.knums .sk') && !document.querySelector('#districts .sk')")


def test_sale_mode_renders_everything_from_api(market, hermetic_server, stats_data):
    page = market
    page.goto(hermetic_server + "/stats")
    _ready(page)

    expect(page.locator("[data-r=k2]")).to_have_text(_ru(stats_data["median_ppsm"]))
    expect(page.locator("[data-r=k3]")).to_have_text(_ru(stats_data["total_listings"]))
    # районы: строки из фикстуры, самый дорогой метр первым, отклонение — числом со знаком
    rows = page.locator("#districts .drow")
    expect(rows).to_have_count(8)
    expect(rows.first).to_contain_text("Медеуский")
    expect(rows.first).to_contain_text(f"960{NB}000{NB}₸/м²")
    expect(rows.first.locator(".tdl")).to_have_text("+30%")
    # в той же строке — аренда и доходность из /api/stats/rent
    med = next(d for d in RENT["by_district"] if d["district"] == "Медеуский")
    expect(rows.first).to_contain_text(f"{_ru(med['median_rent'])}{NB}₸/мес")
    expect(rows.first.locator(".dyl")).to_contain_text(f"≈{round(med['gross_yield_pct'])}%")
    expect(page.locator("[data-meta=districts]")).to_contain_text(f"739{NB}130{NB}₸/м²")
    # недели: точка на каждую неделю фикстуры, подписи из данных
    expect(page.locator("[data-r=tpts] .cpt")).to_have_count(len(stats_data["trend"]))
    expect(page.locator("[data-r=tx] span").first).to_have_text("08.06")
    expect(page.locator("[data-r=tmeta]")).to_contain_text(f"{len(stats_data['trend'])}{NB}недель")
    # гистограмма: вилки по-человечески
    expect(page.locator("[data-r=hist] .hbin")).to_have_count(10)
    expect(page.locator("[data-r=hist] .hlab").first).to_have_text("до 20 млн")
    expect(page.locator("[data-r=hist] .hlab").last).to_have_text("от 250 млн")
    # комнаты: шестикомнатных 2 — строки нет, но это сказано словами
    expect(page.locator("#rooms .drow")).to_have_count(5)
    expect(page.locator("[data-r=rnote]")).to_contain_text("6-комнатных в продаже всего 2")
    # данные старше трёх дней — «данные на …», а не «обновлено»
    expect(page.locator("[data-r=upd]")).to_contain_text("данные на")


def test_switch_changes_mode_url_and_numbers_in_place(market, hermetic_server):
    page = market
    page.goto(hermetic_server + "/stats")
    _ready(page)
    page.evaluate("window.__same = 1")

    page.click(".mswitch [data-set=rent]")
    expect(page).to_have_url(re.compile(r"/stats\?mode=rent$"))
    expect(page.locator("html")).to_have_attribute("data-mode", "rent")
    expect(page.locator(".mswitch [data-set=rent]")).to_have_attribute("aria-pressed", "true")
    r1 = next(r for r in RENT["by_rooms"] if r["rooms"] == 1)["median_rent"]
    expect(page.locator("[data-r=k1]")).to_have_text(_ru(r1))
    expect(page.locator("[data-r=k3]")).to_have_text(_ru(RENT["total_listings"]))
    expect(page.locator("[data-r=hist] .hlab").last).to_have_text("от 1 млн")
    expect(page.locator('footer a.flink[aria-current="page"]')).to_have_attribute("href", "/stats?mode=rent")
    assert page.title().startswith("Аренда квартир")

    page.click(".mswitch [data-set=sale]")
    expect(page).to_have_url(re.compile(r"/stats$"))
    expect(page.locator('footer a.flink[aria-current="page"]')).to_have_attribute("href", "/stats")
    # страница не перезагружалась
    assert page.evaluate("window.__same") == 1


def test_old_rent_url_opens_rent_mode(market, hermetic_server):
    page = market
    page.goto(hermetic_server + "/rent")
    expect(page).to_have_url(re.compile(r"/stats\?mode=rent$"))
    _ready(page)
    expect(page.locator(".mswitch [data-set=rent]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("h1")).to_contain_text("Рынок")


def test_week_chart_is_one_tab_stop_with_arrow_keys(market, hermetic_server, stats_data):
    page = market
    page.goto(hermetic_server + "/stats")
    _ready(page)

    plot = page.locator("[data-r=tplot]")
    plot.focus()
    tip = page.locator("[data-r=ttip]")
    last = stats_data["trend"][-1]
    expect(tip).to_contain_text(f"{_ru(last['median_ppsm'])}{NB}₸/м²")
    page.keyboard.press("Home")
    expect(tip).to_contain_text("неделя с 08.06")
    page.keyboard.press("ArrowRight")
    expect(tip).to_contain_text("неделя с 15.06")
    expect(page.locator("[data-r=tlive]")).to_contain_text("15.06")
    # подсказка стоит ровно на точке линии
    gap = page.evaluate("""() => { const d = document.querySelector('.cdot').getBoundingClientRect();
        const c = [d.left + d.width / 2, d.top + d.height / 2];
        return Math.min(...[...document.querySelectorAll('.cpt')].map(e => { const r = e.getBoundingClientRect();
          return Math.hypot(r.left + r.width / 2 - c[0], r.top + r.height / 2 - c[1]); })); }""")
    assert gap < 1.5
    # гистограмма без кнопок: на всю страницу графиков одна остановка Tab
    assert page.locator("main [tabindex='0']").count() == 1
    assert page.locator(".hist button, .cband").count() == 0


@pytest.mark.parametrize("path", ["/stats", "/stats?mode=rent"])
def test_api_failure_shows_error_instead_of_numbers(hermetic_page, hermetic_server, path):
    page = hermetic_page
    for pattern in ("**/api/stats", "**/api/stats/rent", "**/api/health"):
        page.route(pattern, lambda r: r.fulfill(status=503, body="{}", content_type="application/json"))
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(hermetic_server + path)

    expect(page.locator("[data-r=err]")).to_be_visible()
    expect(page.locator("[data-r=err]")).to_contain_text("временно недоступны")
    expect(page.locator(".knums")).to_be_hidden()
    expect(page.locator("#districts")).to_be_hidden()
    expect(page.locator("[data-r=upd]")).to_have_text("данные недоступны")
    expect(page.locator("h1")).to_contain_text("Рынок")
    assert errors == [], f"необработанные JS-ошибки: {errors}"


@pytest.mark.parametrize("path", ["/stats", "/stats?mode=rent"])
def test_no_console_errors_no_gsap_and_no_horizontal_scroll(market, hermetic_server, path):
    page = market
    page.set_viewport_size({"width": 390, "height": 844})
    errors, gsap = [], []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("request", lambda r: gsap.append(r.url) if "gsap" in r.url.lower() else None)
    page.goto(hermetic_server + path)
    _ready(page)
    page.wait_for_timeout(500)

    assert errors == [], f"консоль не пуста: {errors}"
    assert gsap == [], "GSAP «Рынку» не нужен"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # телефон: первая цифра — на первом экране
    assert page.locator("[data-r=k1]").bounding_box()["y"] < 844
