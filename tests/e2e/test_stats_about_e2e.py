"""Браузерные e2e страниц /stats и /about (герметично, см. conftest.py)."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_stats_renders_summary_districts_and_insights(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/stats")

    # Сводка из /api/stats.
    expect(page.locator("#sum-total")).to_have_text("18 680")
    expect(page.locator("#market-lead")).to_contain_text("Медиана города")
    # Таблица районов: 8 строк-ссылок с deep-link'ами подписки.
    rows = page.locator("#district-table a.mrow")
    expect(rows).to_have_count(8)
    expect(rows.first).to_contain_text("Медеуский")
    href = rows.first.get_attribute("href")
    assert href.startswith("https://t.me/fairprice_kzbot?start=market_")
    # Тренд-график отрисован.
    expect(page.locator("#trend-chart svg")).to_be_visible()
    # Инсайты сформированы из данных.
    expect(page.locator("#insights-list")).to_contain_text("Самая высокая медиана")
    # Структура базы: проценты вторички/новостроек.
    expect(page.locator("#insights-list")).to_contain_text("вторичка")


def test_stats_forecast_section_removed_when_flag_off(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api(forecast_status=404)
    page.goto(hermetic_server + "/stats")
    expect(page.locator("#sum-total")).to_have_text("18 680")

    # 404 от /api/forecast → секция прогноза удаляется из DOM целиком.
    expect(page.locator("#forecast-section")).to_have_count(0)


def test_stats_survives_total_api_failure(hermetic_page, hermetic_server):
    """Все /api/* упали → страница показывает заглушки, а не белый экран."""
    page = hermetic_page
    for pattern in ("**/api/stats", "**/api/heatmap"):
        page.route(pattern, lambda r: r.fulfill(status=503, body="{}", content_type="application/json"))
    page.route("**/api/forecast", lambda r: r.fulfill(status=404, body="{}", content_type="application/json"))

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(hermetic_server + "/stats")

    expect(page.locator("#market-lead")).to_contain_text("временно недоступна")
    expect(page.locator("#trend-chart")).to_contain_text("Данные рынка временно недоступны")
    expect(page.locator("#district-table")).to_contain_text("временно недоступ")
    assert errors == [], f"необработанные JS-ошибки: {errors}"


def test_stats_no_console_errors_on_happy_path(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    mock_api()  # forecast 404 — штатный режим (фича-флаг выключен)
    page.goto(hermetic_server + "/stats")
    expect(page.locator("#sum-total")).to_have_text("18 680")

    # Единственный допустимый шум — сетевой лог 404 от /api/forecast:
    # browser логирует любой не-2xx fetch, фронт его штатно обрабатывает.
    real_errors = [e for e in errors if "/api/forecast" not in e and "404" not in e]
    assert real_errors == [], f"консоль не пуста: {real_errors}"


def test_about_page_renders_and_dynamic_numbers_load(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(hermetic_server + "/about")

    expect(page.locator("h1")).to_contain_text("Оценка, которую можно проверить")
    # Числа базы подтягиваются из /api/stats и /api/health.
    expect(page.locator("body")).to_contain_text("18 680")
    assert errors == []


def test_nav_between_pages_keeps_theme(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    # Включаем противоположную тему и идём по навигации.
    page.click(".theme-btn")
    dark_home = page.evaluate("document.body.classList.contains('dark')")

    page.click("nav.links a[href='/stats']")
    expect(page.locator("#sum-total")).to_have_text("18 680")
    assert page.evaluate("document.body.classList.contains('dark')") == dark_home

    page.click("nav.links a[href='/about']")
    expect(page.locator("h1")).to_contain_text("Оценка")
    assert page.evaluate("document.body.classList.contains('dark')") == dark_home
