"""Браузерные e2e главной страницы: реальный Chromium против реального uvicorn.

Всё герметично — /api/* замоканы Playwright-роутами (см. conftest.py), внешние
CDN заглушены. Проверяется то, чего не видят string-тесты test_home_*.py:
реальное исполнение JS, состояния DOM, порядок загрузки, обработка ошибок сети.

Разметка после редизайна (соответствие старым именам — для читающего диффы):
форма ``form[data-check]`` с полем ``#lotUrl`` и кнопкой ``.gobtn``, ошибка —
``[data-err]`` с классом ``on``, живой регион ``#checkStatus``, карточка разбора
``.sheet`` (вердикт ``.vbig/.vsub/.vpct``, цифры ``#count/#rFair/#rPpsm``,
шкала ``.rband/.rmk``, факторы ``#fxList .fx``, предупреждения ``#rWarn``,
история ``#rHist``, аналоги ``#rSim``, подвал ``.rfoot``), живые числа рынка —
элементы с ``data-l``.
"""

import json
from copy import deepcopy

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

LOT_URL = "https://krisha.kz/a/show/761891663"


def submit(page, url: str = LOT_URL):
    """Заполнить форму героя и отправить её."""
    page.fill("#lotUrl", url)
    page.click("form[data-check]:has(#lotUrl) .gobtn")


def wait_ready(page):
    """Страница отработала загрузку живых чисел — можно кликать."""
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")


def test_home_loads_market_stats_and_offers_demo(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/")

    # Число лотов из /api/stats подставлено во все места разметки.
    wait_ready(page)
    expect(page.locator("[data-l=ppsm]").first).to_have_text("739 130")
    # Форма готова к работе, ошибок нет.
    expect(page.locator("#lotUrl")).to_be_editable()
    expect(page.locator("form[data-check]:has(#lotUrl) .gobtn")).to_be_enabled()
    expect(page.locator("[data-err]").first).not_to_have_class("inerr on")
    # Кнопка «показать на примере» появилась — /api/demo ответил.
    expect(page.locator("[data-demo]")).to_be_visible()
    # Карточка разбора на месте со снимком сборки (страница не пустая до оценки).
    expect(page.locator(".sheet")).to_be_visible()
    expect(page.locator("#checkStatus")).to_have_text("")


def test_home_client_side_url_validation(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    calls = []
    page.route("**/api/predict", lambda r: (calls.append(r.request.url), r.abort()))
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page, "https://example.com/not-krisha")

    err = page.locator("form[data-check]:has(#lotUrl) + [data-err]")
    expect(err).to_contain_text("Нужна ссылка на объявление вида krisha.kz/a/show/1012607661")
    expect(err).to_have_class("inerr on")
    expect(page.locator("#lotUrl")).to_have_attribute("aria-invalid", "true")
    # Запрос на сервер НЕ ушёл.
    assert calls == [], f"невалидный URL всё же ушёл на сервер: {calls}"

    # Правка поля гасит ошибку.
    page.fill("#lotUrl", LOT_URL)
    expect(err).to_have_class("inerr")
    expect(page.locator("#lotUrl")).not_to_have_attribute("aria-invalid", "true")


def test_home_predict_fair_renders_full_report(hermetic_page, mock_api, hermetic_server, predict_fair):
    page = hermetic_page
    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    sheet = page.locator(".sheet")
    # Вердикт FAIR → «В рынке», без класса ошибки.
    expect(sheet.locator(".vbig")).to_have_text("В рынке")
    expect(sheet.locator(".vpct")).to_have_text("+1,8%")
    expect(page.locator(".verdict")).to_have_class("verdict warn")
    # Цифры: цена объявления, справедливая, цена метра.
    expect(page.locator("#count")).to_contain_text("47 000 000")
    expect(page.locator("#rFair")).to_contain_text("46 190 000")
    expect(page.locator("#rPpsm")).to_contain_text("642 955")  # 47 000 000 / 73.1
    expect(page.locator("#rBand")).to_contain_text("интервал модели ±")
    expect(page.locator("#rEndLo")).to_contain_text("40,5 млн")
    expect(page.locator("#rEndHi")).to_contain_text("52,4 млн")
    # Метка объявления на шкале модели.
    expect(page.locator(".rmk.ask span")).to_contain_text("объявление · 47,0 млн")
    expect(page.locator(".rmk.fair span")).to_contain_text("справедливая · 46,2 млн")
    # Факторы: 5 штук, русские подписи, направление словом.
    factors = page.locator("#fxList .fx")
    expect(factors).to_have_count(5)
    expect(factors.first).to_have_class("fx pos")
    expect(factors.first.locator(".fxn")).to_contain_text("Площадь")
    expect(factors.first.locator(".fxd")).to_contain_text("повышает")
    expect(factors.first.locator(".fxv")).to_have_text("+10,0 млн")
    # История цены: две точки, продавец снизил цену.
    expect(page.locator("#rHist")).to_be_visible()
    expect(page.locator("#rHist")).to_contain_text("продавец снизил цену")
    # Похожие лоты — ссылки на krisha.kz.
    expect(page.locator("#rSim")).to_be_visible()
    expect(page.locator("#rSim a[href*='krisha.kz/a/show/']")).to_have_count(3)
    # Подвал: ссылка на объявление, слежение в боте, «поделиться».
    foot = page.locator(".rfoot")
    expect(foot.locator("a[href='%s']" % LOT_URL)).to_be_visible()
    expect(foot.locator("a[href='https://t.me/fairprice_kzbot?start=track_761891663']")).to_be_visible()
    expect(foot.locator(".rshare")).to_be_visible()
    expect(foot).to_contain_text("похожие уходят за")
    # Источник и живой статус.
    expect(page.locator("#repSrc")).to_contain_text("krisha.kz/a/show/761891663")
    expect(page.locator("#checkStatus")).to_have_text("Готово: В рынке, +1,8%")
    # Предупреждений нет — блок скрыт.
    expect(page.locator("#rWarn")).to_be_hidden()


def test_home_predict_overpriced_verdict_label(hermetic_page, mock_api, hermetic_server, predict_overpriced):
    page = hermetic_page
    mock_api(predict=predict_overpriced)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    expect(page.locator(".vbig")).to_have_text("Переплата")
    expect(page.locator(".vsub")).to_contain_text("есть за что торговаться")
    expect(page.locator(".vpct")).to_have_text("+29,9%")
    expect(page.locator(".verdict")).to_have_class("verdict bad")


def test_home_predict_good_deal_shows_scam_warning(hermetic_page, mock_api, hermetic_server, predict_good_deal):
    page = hermetic_page
    mock_api(predict=predict_good_deal)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    expect(page.locator(".vbig")).to_have_text("Выгодно")
    expect(page.locator(".vpct")).to_have_text("−28,5%")
    warn = page.locator("#rWarn")
    expect(warn).to_be_visible()
    expect(warn).to_contain_text("Цена сильно ниже рынка")
    expect(warn).to_contain_text("ниже нижней границы интервала модели")
    expect(warn).to_contain_text("Не вносите задаток")


def test_home_duplicate_listing_warns_about_repost(hermetic_page, mock_api, hermetic_server, predict_fair):
    """duplicate_of из API → предупреждение о перезаливе объявления."""
    page = hermetic_page
    data = deepcopy(predict_fair)
    data["duplicate_of"] = 1012607661
    mock_api(predict=data)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    warn = page.locator("#rWarn")
    expect(warn).to_be_visible()
    expect(warn).to_contain_text("Похоже на перезалив объявления №1012607661")


def test_home_predict_no_price_renders_model_estimate(hermetic_page, mock_api, hermetic_server, predict_no_price):
    """Объявление без цены: вердикта нет, но оценка модели показана честно —

    без нулевой «цены в объявлении», без «−0,0%» и без метки лота на шкале.
    """
    page = hermetic_page
    mock_api(predict=predict_no_price)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    expect(page.locator(".vbig")).to_have_text("Оценка модели")
    expect(page.locator(".vsub")).to_contain_text("в объявлении нет цены")
    expect(page.locator(".vpct")).to_be_hidden()
    expect(page.locator("#count")).to_have_text("—", use_inner_text=True)
    expect(page.locator("#rFair")).to_contain_text("46 190 000")
    expect(page.locator("#rPpsm")).to_contain_text("631 874")  # 46 190 000 / 73.1
    expect(page.locator(".rmk.ask")).to_be_hidden()
    expect(page.locator(".rmk.fair span")).to_contain_text("справедливая · 46,2 млн")
    expect(page.locator("#checkStatus")).to_have_text("Готово: Оценка модели")


def test_home_unknown_factor_is_hidden_not_raw_snake_case(hermetic_page, mock_api, hermetic_server, predict_fair):
    """Незнакомый признак модели не должен утекать в интерфейс сырым ключом."""
    page = hermetic_page
    data = deepcopy(predict_fair)
    data["top_factors"] = data["top_factors"] + [
        {"feature": "some_new_secret_feature", "impact": 0.1, "impact_pct": 3.0, "impact_tenge": 900000.0}
    ]
    mock_api(predict=data)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    expect(page.locator("#fxList .fx")).to_have_count(5)
    expect(page.locator("#fxList")).not_to_contain_text("some_new_secret_feature")


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
    wait_ready(page)

    submit(page, "https://krisha.kz/a/show/123456789")

    err = page.locator("form[data-check]:has(#lotUrl) + [data-err]")
    expect(err).to_contain_text("Сервис сейчас не отвечает. Попробуйте ещё раз через минуту")
    expect(err).to_have_class("inerr on")
    expect(page.locator("#checkStatus")).to_have_text("Не удалось получить оценку")
    # Кнопка разблокирована для повторной попытки, скелетон снят.
    expect(page.locator("form[data-check]:has(#lotUrl) .gobtn")).to_be_enabled()
    expect(page.locator(".sheet")).not_to_have_class("sheet busy")


def test_home_unparsable_listing_422_message(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.route("**/api/predict", lambda r: r.fulfill(
        status=422, body=json.dumps({"detail": "bad"}), content_type="application/json"))
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page, "https://krisha.kz/a/show/123456789")

    expect(page.locator("form[data-check]:has(#lotUrl) + [data-err]")).to_contain_text(
        "Такое объявление не открывается — проверьте ссылку")


def test_home_rate_limit_429_message_reaches_user(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.route("**/api/predict", lambda r: r.fulfill(
        status=429, body=json.dumps({"detail": "too many"}), content_type="application/json"))
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page, "https://krisha.kz/a/show/123456789")

    expect(page.locator("form[data-check]:has(#lotUrl) + [data-err]")).to_contain_text(
        "Слишком много запросов подряд. Попробуйте через минуту")


def test_home_busy_state_while_model_thinks(hermetic_page, mock_api, hermetic_server, predict_fair):
    """Долгий ответ модели: кнопка занята, карточка в скелетоне, статус озвучен."""
    page = hermetic_page
    mock_api()

    def slow(route):
        page.wait_for_timeout(1500)
        route.fulfill(status=200, body=json.dumps(predict_fair), content_type="application/json")

    page.route("**/api/predict", slow)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    btn = page.locator("form[data-check]:has(#lotUrl) .gobtn")
    expect(btn).to_be_disabled()
    expect(btn).to_have_text("Считаем…")
    expect(page.locator(".sheet")).to_have_class("sheet busy")
    expect(page.locator("#checkStatus")).to_have_text("Считаем оценку объявления")
    expect(page.locator(".waitmsg")).to_contain_text("Считаем: тянем объявление")

    # После ответа всё возвращается в рабочее состояние.
    expect(page.locator(".vbig")).to_have_text("В рынке")
    expect(btn).to_be_enabled()
    expect(page.locator(".sheet")).not_to_have_class("sheet busy")
    expect(page.locator(".waitmsg")).not_to_have_class("waitmsg on")


def test_home_survives_dead_market_api(hermetic_page, hermetic_server):
    """/api/stats и /api/health упали → страница живёт снимком сборки.

    Главное: не белый экран и не сломанная форма — оценка по-прежнему доступна,
    а цифры честно подписаны как несвежие.
    """
    page = hermetic_page
    for pattern in ("**/api/stats", "**/api/health", "**/api/demo"):
        page.route(pattern, lambda r: r.fulfill(status=503, body="{}", content_type="application/json"))

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(hermetic_server + "/")

    expect(page.locator("[data-l=age]").first).to_have_text("цифры из последнего успешного обновления")
    # Снимок сборки на месте: число лотов не обнулилось и не превратилось в «—».
    expect(page.locator("[data-l=total]").first).to_contain_text("729")
    # Кнопки демо нет (без /api/demo), но форма работает.
    expect(page.locator("[data-demo]")).to_be_hidden()
    expect(page.locator("form[data-check]:has(#lotUrl) .gobtn")).to_be_enabled()
    assert errors == [], f"необработанные JS-ошибки: {errors}"


def test_home_demo_button_fills_input_and_submits(hermetic_page, mock_api, hermetic_server, predict_fair):
    page = hermetic_page
    mock_api(predict=predict_fair, demo_url=LOT_URL)
    page.goto(hermetic_server + "/")
    expect(page.locator("[data-demo]")).to_be_visible()

    page.click("[data-demo]")

    expect(page.locator("#lotUrl")).to_have_value(LOT_URL)
    expect(page.locator(".vbig")).to_have_text("В рынке")


def test_home_hash_deeplink_runs_report(hermetic_page, mock_api, hermetic_server, predict_fair):
    """Возврат с подстраницы по /#check=<id> сразу считает оценку."""
    page = hermetic_page
    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/#check=761891663")

    expect(page.locator("#lotUrl")).to_have_value(LOT_URL)
    expect(page.locator(".vbig")).to_have_text("В рынке")


def test_home_share_report_copies_text(hermetic_page, mock_api, hermetic_server, predict_fair):
    """«Поделиться отчётом» без navigator.share кладёт текст отчёта в буфер."""
    page = hermetic_page
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.add_init_script("delete Navigator.prototype.share;")
    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    wait_ready(page)
    submit(page)
    expect(page.locator(".vbig")).to_have_text("В рынке")

    page.click(".rshare")

    expect(page.locator(".rshare")).to_have_text("скопировано")
    expect(page.locator("#checkStatus")).to_have_text("Отчёт скопирован")
    text = page.evaluate("navigator.clipboard.readText()")
    assert "Справедливая оценка: 46\u00a0190\u00a0000 ₸" in text
    assert LOT_URL in text


def test_home_theme_toggle_persists_in_local_storage(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.goto(hermetic_server + "/")
    wait_ready(page)

    was = page.get_attribute("html", "data-theme")
    assert was in ("dark", "light")
    page.click("[data-theme-toggle]")
    now = page.get_attribute("html", "data-theme")
    assert now != was
    # Ключ прода и дубль старого ключа — оба обновились.
    assert page.evaluate("localStorage.getItem('bagam-theme')") == now
    assert page.evaluate("localStorage.getItem('kfp-theme')") == now
    # Подпись переключателя рассказывает текущую тему.
    expect(page.locator("[data-theme-label]").first).to_have_text(
        "светлая" if now == "light" else "тёмная")

    page.reload()
    wait_ready(page)
    assert page.get_attribute("html", "data-theme") == now


def test_home_no_console_errors_on_full_flow(hermetic_page, mock_api, hermetic_server, predict_fair):
    """Весь happy-path не должен сыпать ошибки в консоль."""
    page = hermetic_page
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    wait_ready(page)
    submit(page)
    expect(page.locator(".vbig")).to_have_text("В рынке")

    assert errors == [], f"консоль не пуста: {errors}"


def test_home_mobile_viewport_no_horizontal_scroll(hermetic_page, mock_api, hermetic_server, predict_fair):
    page = hermetic_page
    page.set_viewport_size({"width": 390, "height": 844})
    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    submit(page)
    expect(page.locator(".vbig")).to_have_text("В рынке")
    # И с отчётом тоже без горизонтального скролла.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_home_mobile_menu_opens_and_closes_on_escape(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    page.set_viewport_size({"width": 390, "height": 844})
    mock_api()
    page.goto(hermetic_server + "/")
    wait_ready(page)

    burger = page.locator(".burg")
    menu = page.locator("#mmenu")
    expect(burger).to_have_attribute("aria-expanded", "false")

    burger.click()
    expect(burger).to_have_attribute("aria-expanded", "true")
    expect(menu).to_have_attribute("aria-hidden", "false")
    expect(menu.locator("a[href='/stats']")).to_be_visible()

    page.keyboard.press("Escape")
    expect(burger).to_have_attribute("aria-expanded", "false")
    expect(menu).to_have_attribute("aria-hidden", "true")
