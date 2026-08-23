"""Браузерные e2e страниц /stats, /about и 404 (герметично, см. conftest.py).

Разметка после редизайна: числа рынка приходят в HTML снимком сборки, а затем
перерисовываются живым /api/stats — поэтому тесты проверяют именно живые
значения из фикстуры, а на упавшем API — что снимок остался и подписан честно.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_stats_renders_summary_districts_and_charts(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/stats")

    # Сводка из /api/stats.
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")
    # Районы: 8 строк из фикстуры, первый — самый дорогой метр.
    rows = page.locator(".drows .drow")
    expect(rows).to_have_count(8)
    expect(rows.first).to_contain_text("Медеуский")
    expect(rows.first).to_contain_text("960 000 ₸/м²")
    expect(rows.first.locator(".ddt")).to_have_text("+30%")
    expect(page.locator("[data-meta=districts]")).to_contain_text("739 130 ₸/м²")
    note = page.locator("[data-note=districts]")
    expect(note).to_contain_text("Дороже всего метр — в Медеуском и Бостандыкском")
    expect(note).to_contain_text("Больше всего выбора в Алатауском")
    # График недель: 10 точек тренда + столбики выборки + подписи недель.
    expect(page.locator(".cbands .cband")).to_have_count(10)
    expect(page.locator(".cvols .cvol")).to_have_count(10)
    expect(page.locator(".cx span").first).to_have_text("08.06")
    expect(page.locator(".cmedtag")).to_contain_text("медиана города · 739 130 ₸/м²")
    # Гистограмма цен: 10 корзин, подписи диапазонов.
    expect(page.locator(".hist .hcol")).to_have_count(10)
    expect(page.locator(".hx span").first).to_have_text("0–20 млн")
    expect(page.locator("[data-note=hist]")).to_contain_text("Плотнее всего рынок в диапазоне")
    # Комнатность: строки только для сегментов с n >= 100 (шестикомнатных в базе 2).
    expect(page.locator("[data-rooms] .rrow")).to_have_count(5)
    expect(page.locator("[data-rooms] .rrow").first).to_contain_text("1-комнатные")
    # Структура базы: вторичка/новостройки.
    expect(page.locator(".catl")).to_contain_text("вторичка")
    expect(page.locator(".catl")).to_contain_text("18 447")
    expect(page.locator(".catl")).to_contain_text("233")


def test_stats_trend_tooltip_exposes_week_numbers(hermetic_page, mock_api, hermetic_server):
    """Точки графика доступны с клавиатуры и подписаны для скринридера."""
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/stats")
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")

    band = page.locator(".cbands .cband").first
    expect(band).to_have_attribute("aria-label", "Неделя 08.06: 739 514 ₸ за м², 7 055 лотов в выборке")
    band.focus()
    expect(band.locator(".ctip")).to_contain_text("739 514 ₸/м²")


def test_stats_survives_total_api_failure(hermetic_page, hermetic_server):
    """Все /api/* упали → остаётся снимок сборки, а не белый экран.

    Живые цифры честно подписаны как несвежие, JS-ошибок нет.
    """
    page = hermetic_page
    for pattern in ("**/api/stats", "**/api/health", "**/api/heatmap"):
        page.route(pattern, lambda r: r.fulfill(status=503, body="{}", content_type="application/json"))

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(hermetic_server + "/stats")

    expect(page.locator("[data-l=age]").first).to_have_text("цифры из последнего успешного обновления")
    # Снимок сборки на месте: районы, график и гистограмма нарисованы.
    expect(page.locator(".drows .drow").first).to_contain_text("Медеуский")
    expect(page.locator(".cbands .cband").first).to_be_visible()
    expect(page.locator(".hist .hcol").first).to_be_visible()
    expect(page.locator("h1")).to_contain_text("Рынок")
    assert errors == [], f"необработанные JS-ошибки: {errors}"


def test_stats_no_console_errors_on_happy_path(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    mock_api()  # forecast 404 — штатный режим (фича-флаг выключен)
    page.goto(hermetic_server + "/stats")
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")

    # Единственный допустимый шум — сетевой лог 404 от /api/forecast:
    # browser логирует любой не-2xx fetch, фронт его штатно обрабатывает.
    real_errors = [e for e in errors if "/api/forecast" not in e and "404" not in e]
    assert real_errors == [], f"консоль не пуста: {real_errors}"


def test_stats_mobile_viewport_no_horizontal_scroll(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    page.set_viewport_size({"width": 390, "height": 844})
    mock_api()
    page.goto(hermetic_server + "/stats")
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


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
    expect(page.locator("[data-l=r2]").first).to_have_text("0.937")
    expect(page.locator("[data-l=age]").first).to_have_text("обновлено 3 ч назад")
    assert errors == []


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
    resp = page.goto(hermetic_server + "/no-such-page")

    assert resp.status == 404
    expect(page.locator("h1")).to_contain_text("Такой страницы нет")
    expect(page.locator(".nfnum")).to_contain_text("404")
    # Обе кнопки возврата на месте и ведут внутрь сайта.
    expect(page.locator(".nfbtn").first).to_have_attribute("href", "/")
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")


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
