"""Браузерные e2e главной страницы: реальный Chromium против реального uvicorn.

Всё герметично — /api/* замоканы Playwright-роутами (см. conftest.py), внешние
CDN заглушены. Проверяется то, чего не видят string-тесты test_home_*.py:
реальное исполнение JS, состояния DOM, порядок загрузки, обработка ошибок сети.
"""

import json

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_home_loads_market_stats_and_unlocks_evaluate(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/")

    # Пока статистика грузится, кнопка заблокирована; после — разблокируется.
    expect(page.locator("#btn")).to_be_enabled()
    # Заголовок и число лотов из /api/stats подставлены в hero.
    expect(page.locator("#market-count")).to_contain_text("18 680")
    # Скелетон графика скрыт, сам график построен.
    expect(page.locator("#market-skeleton")).to_be_hidden()
    expect(page.locator(".chart-box svg")).to_be_visible()
    # Плашка «Загружаем свежие данные» исчезла.
    expect(page.locator("#market-loadnote")).to_be_hidden()
    # Пустой отчёт с кнопкой демо (demoUrl пришёл из /api/demo).
    expect(page.locator("#receipt")).to_contain_text("Здесь появится отчёт об оценке")
    expect(page.locator("#demo-link")).to_be_visible()


def test_home_client_side_url_validation(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    page.fill("#url", "https://example.com/not-krisha")
    page.click("#btn")

    status = page.locator("#status")
    expect(status).to_contain_text("не ссылка на объявление")
    expect(status).to_have_class("fstat err")
    # Запрос на сервер НЕ ушёл — отчёт остался пустым.
    expect(page.locator("#receipt")).to_contain_text("оценка ещё не выполнялась")


def test_home_predict_fair_renders_full_receipt(hermetic_page, mock_api, hermetic_server, predict_fair):
    page = hermetic_page
    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    page.fill("#url", "https://krisha.kz/a/show/761891663")
    page.click("#btn")

    receipt = page.locator("#receipt")
    expect(receipt).to_contain_text("3-комнатная квартира")
    # Вердикт FAIR → «справедливо», цена и оценка отформатированы.
    expect(receipt.locator(".scale-l .cur")).to_have_text("справедливо")
    expect(receipt.locator(".price")).to_contain_text("47 000 000")
    expect(receipt).to_contain_text("46,2 млн")
    # Факторы: русские подписи и стрелки направлений. Класс .factors носит и
    # блок истории цены, поэтому фильтруем по .fname — он только у факторов.
    factors = receipt.locator(".factors .fr").filter(has=receipt.page.locator(".fname"))
    expect(factors.first).to_contain_text("Площадь")
    expect(factors.first).to_contain_text("↑")
    # История цены (2 точки, падение) отрисована.
    expect(receipt.locator(".price-history")).to_contain_text("снизил цену")
    # Аналоги — ссылки на krisha.kz.
    analog_links = receipt.locator("a.fr[href*='krisha.kz/a/show/']")
    assert analog_links.count() >= 3
    # Кнопка слежки — deep-link с id лота.
    expect(receipt.locator(".btn-bell")).to_have_attribute(
        "href", "https://t.me/fairprice_kzbot?start=track_761891663"
    )
    # Маркер лота появился на hero-графике и в легенде.
    expect(page.locator("#lot-legend")).to_contain_text("этот лот")


def test_home_predict_overpriced_verdict_label(hermetic_page, mock_api, hermetic_server, predict_overpriced):
    page = hermetic_page
    mock_api(predict=predict_overpriced)
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    page.fill("#url", "https://krisha.kz/a/show/761891663")
    page.click("#btn")

    receipt = page.locator("#receipt")
    expect(receipt.locator(".scale-l .cur")).to_have_text("завышено")
    expect(receipt).to_contain_text("выше справедливой оценки")


def test_home_predict_good_deal_shows_scam_warning(hermetic_page, mock_api, hermetic_server, predict_good_deal):
    page = hermetic_page
    mock_api(predict=predict_good_deal)
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    page.fill("#url", "https://krisha.kz/a/show/761891663")
    page.click("#btn")

    receipt = page.locator("#receipt")
    expect(receipt.locator(".scale-l .cur")).to_have_text("занижено")
    # Бейдж риска + предупреждение о задатке.
    expect(receipt.locator("#flags-box")).to_contain_text("Сильно ниже рынка")
    expect(receipt.locator(".scam-note")).to_contain_text("задаток")


def test_home_predict_no_price_renders_model_estimate(hermetic_page, mock_api, hermetic_server, predict_no_price):
    page = hermetic_page
    mock_api(predict=predict_no_price)
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    page.fill("#url", "https://krisha.kz/a/show/761891663")
    page.click("#btn")

    receipt = page.locator("#receipt")
    expect(receipt).to_contain_text("Оценка модели")
    expect(receipt.locator(".scale-l .cur")).to_have_text("без цены")


def test_home_server_error_shows_friendly_status(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.route(
        "**/api/predict",
        lambda r: r.fulfill(
            status=502,
            body=json.dumps({"detail": "Не удалось обработать объявление"}),
            content_type="application/json",
        ),
    )
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    page.fill("#url", "https://krisha.kz/a/show/123456789")
    page.click("#btn")

    status = page.locator("#status")
    expect(status).to_contain_text("Не удалось обработать объявление")
    expect(status).to_have_class("fstat err")
    # Кнопка разблокирована для повторной попытки.
    expect(page.locator("#btn")).to_be_enabled()


def test_home_rate_limit_429_message_reaches_user(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.route(
        "**/api/predict",
        lambda r: r.fulfill(
            status=429,
            body=json.dumps({"detail": "Слишком много запросов, подожди минуту"}),
            content_type="application/json",
        ),
    )
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    page.fill("#url", "https://krisha.kz/a/show/123456789")
    page.click("#btn")

    expect(page.locator("#status")).to_contain_text("подожди минуту")


def test_home_stats_failure_still_allows_evaluate(hermetic_page, hermetic_server):
    """/api/stats упал → плашка об ошибке, но оценка разблокирована (fail-soft)."""
    page = hermetic_page
    page.route("**/api/stats", lambda r: r.fulfill(status=503, body="{}", content_type="application/json"))
    page.route("**/api/demo", lambda r: r.fulfill(status=503, body="{}", content_type="application/json"))
    page.route("**/api/forecast", lambda r: r.fulfill(status=404, body="{}", content_type="application/json"))
    page.goto(hermetic_server + "/")

    expect(page.locator("#btn")).to_be_enabled()
    note = page.locator("#market-loadnote")
    expect(note).to_be_visible()
    expect(note).to_contain_text("оценку можно запустить")
    # Кнопки демо нет (без /api/demo), но пустой отчёт отрисован.
    expect(page.locator("#receipt")).to_contain_text("Здесь появится отчёт об оценке")
    expect(page.locator("#demo-link")).to_be_hidden()


def test_home_demo_button_fills_input_and_submits(hermetic_page, mock_api, hermetic_server, predict_fair):
    page = hermetic_page
    mock_api(predict=predict_fair, demo_url="https://krisha.kz/a/show/761891663")
    page.goto(hermetic_server + "/")
    expect(page.locator("#demo-link")).to_be_visible()

    page.click("#demo-link")

    expect(page.locator("#url")).to_have_value("https://krisha.kz/a/show/761891663")
    expect(page.locator("#receipt .scale-l .cur")).to_have_text("справедливо")


def test_home_theme_toggle_persists_in_local_storage(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    was_dark = page.evaluate("document.body.classList.contains('dark')")
    page.click(".theme-btn")
    now_dark = page.evaluate("document.body.classList.contains('dark')")
    assert now_dark != was_dark
    stored = page.evaluate("localStorage.getItem('bagam-theme')")
    assert stored == ("dark" if now_dark else "light")

    # После перезагрузки тема сохраняется.
    page.reload()
    expect(page.locator("#btn")).to_be_enabled()
    assert page.evaluate("document.body.classList.contains('dark')") == now_dark


def test_home_factor_toggle_expands_hidden_factors(hermetic_page, mock_api, hermetic_server, predict_fair):
    page = hermetic_page
    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()
    page.fill("#url", "https://krisha.kz/a/show/761891663")
    page.click("#btn")

    more = page.locator(".more")
    expect(more).to_be_visible()
    # 5 факторов → скрыт 1: числительное согласовано («фактор», не «факторов»).
    expect(more).to_contain_text("Ещё 1 фактор")
    expect(more).to_have_attribute("aria-expanded", "false")
    # 5 факторов: 4 видимых + 1 скрытый (.xfr).
    hidden_factor = page.locator(".factors .xfr").first
    expect(hidden_factor).to_be_hidden()

    more.click()
    expect(more).to_have_attribute("aria-expanded", "true")
    expect(hidden_factor).to_be_visible()


def test_home_no_console_errors_on_full_flow(hermetic_page, mock_api, hermetic_server, predict_fair):
    """Весь happy-path не должен сыпать ошибки в консоль."""
    page = hermetic_page
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()
    page.fill("#url", "https://krisha.kz/a/show/761891663")
    page.click("#btn")
    expect(page.locator("#receipt .scale-l .cur")).to_have_text("справедливо")

    assert errors == [], f"консоль не пуста: {errors}"


def test_home_mobile_viewport_no_horizontal_scroll(hermetic_page, mock_api, hermetic_server, predict_fair):
    page = hermetic_page
    page.set_viewport_size({"width": 375, "height": 812})
    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    expect(page.locator("#btn")).to_be_enabled()

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    page.fill("#url", "https://krisha.kz/a/show/761891663")
    page.click("#btn")
    expect(page.locator("#receipt .scale-l .cur")).to_have_text("справедливо")
    # И с отчётом тоже без горизонтального скролла.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
