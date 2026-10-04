"""Браузерные e2e страниц /about и 404 и переходов между страницами (герметично, см. conftest.py).

Страница «Рынок» (/stats, продажа и аренда) — в test_market_e2e.py.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_about_page_renders_and_dynamic_numbers_load(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(hermetic_server + "/about")

    expect(page.locator("h1")).to_contain_text("которую можно")
    # Числа базы подтягиваются из /api/stats и /api/health.
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")
    expect(page.locator("[data-l=mape]").first).to_have_text("7,6%")
    expect(page.locator("[data-l=mdape]").first).to_have_text("5,1%")
    expect(page.locator("[data-l=age]").first).to_have_text("обновлено 3 ч назад")
    # «±N млн ₸ для квартиры за 40 млн» — из той же средней ошибки: 7,6% × 40 млн
    expect(page.locator("[data-x40]")).to_have_text("3,0")
    assert errors == []


def test_about_money_error_follows_live_mape(hermetic_page, mock_api, hermetic_server, health_data):
    page = hermetic_page
    mock_api(health=dict(health_data, model_error_pct=10.0))
    page.goto(hermetic_server + "/about")

    expect(page.locator("[data-l=mape]").first).to_have_text("10,0%")
    expect(page.locator("[data-x40]")).to_have_text("4,0")


def test_about_keeps_snapshot_age_when_health_has_no_data_age(hermetic_page, mock_api, hermetic_server, health_data):
    """Сервер не знает возраст данных → остаётся подпись сборки, а не пустое место."""
    page = hermetic_page
    mock_api(health=dict(health_data, data_age_hours=None))
    page.goto(hermetic_server + "/about")

    expect(page.locator("[data-l=mape]").first).to_have_text("7,6%")
    # текст снимка сборки не затёрт пустой строкой
    expect(page.locator("[data-l=age]").first).to_have_text("обновление ежедневно")


def test_404_page_is_branded_and_leads_back(hermetic_page, mock_api, hermetic_server):
    """Неизвестный адрес — своя страница со ссылками, а не голый JSON."""
    page = hermetic_page
    mock_api()
    api = []
    page.on("request", lambda r: api.append(r.url) if "/api/" in r.url else None)
    resp = page.goto(hermetic_server + "/no-such-page", wait_until="load")

    assert resp.status == 404
    # живых цифр на 404 нет: ни запросов к API, ни вечных скелетонов, цифры подвала спрятаны
    page.wait_for_timeout(600)
    assert api == [], api
    expect(page.locator(".sk")).to_have_count(0)
    expect(page.locator("footer .flive").first).to_be_hidden()
    expect(page.locator("footer [data-l=age]")).to_have_text("обновление ежедневно")
    expect(page.locator("h1")).to_contain_text("Такой страницы нет")
    expect(page.locator(".nfnum")).to_contain_text("404")
    # Ссылки на разделы на месте и ведут внутрь сайта; поле для ссылки — как на других страницах.
    expect(page.locator(".nfbtn").first).to_have_attribute("href", "/")
    expect(page.locator("form[data-jump] input")).to_be_visible()
    # в заголовке больше нет живого числа, а адрес без номера лота не предлагает «Проверить объявление»
    expect(page.locator("h1 [data-l]")).to_have_count(0)
    expect(page.locator("#nflot")).to_be_hidden()


@pytest.mark.parametrize("path,lot", [("/a/show/1012345678", "1012345678"),
                                      ("/krisha.kz/a/show/761891663?x=1", "761891663"),
                                      ("/old/1012607661/", "1012607661")])
def test_404_offers_to_check_listing_from_the_address(hermetic_page, mock_api, hermetic_server, path, lot):
    page = hermetic_page
    mock_api()
    resp = page.goto(hermetic_server + path)

    assert resp.status == 404
    link = page.locator("#nflot a")
    expect(link).to_be_visible()
    expect(link).to_contain_text(f"Проверить объявление {lot}")
    expect(link).to_have_attribute("href", f"/#check={lot}")


def test_nav_between_pages_keeps_theme(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/")
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")

    # Включаем противоположную тему и идём по навигации.
    page.click("[data-theme-toggle]")
    theme = page.get_attribute("html", "data-theme")

    page.click("nav a[href='/stats']")
    expect(page.locator("h1")).to_contain_text("Рынок")
    assert page.get_attribute("html", "data-theme") == theme

    page.click("nav a[href='/about']")
    expect(page.locator("h1")).to_contain_text("которую можно")
    assert page.get_attribute("html", "data-theme") == theme

    page.click("nav a[href='/']")
    expect(page.locator("#lotUrl")).to_be_visible()
    assert page.get_attribute("html", "data-theme") == theme
