"""Браузерные e2e главной страницы: реальный Chromium против реального uvicorn.

Всё герметично — /api/* замоканы Playwright-роутами (см. conftest.py), внешние
CDN заглушены. Проверяется то, чего не видят string-тесты test_home_*.py:
реальное исполнение JS, состояния DOM, порядок загрузки, обработка ошибок сети.

Разметка после редизайна (соответствие старым именам — для читающего диффы):
форма ``form[data-check]`` с полем ``#lotUrl`` и кнопкой ``.gobtn``, ошибка ввода —
``[data-err]`` с классом ``on``, живой регион ``#checkStatus``, карточка разбора
``.sheet`` (плашка примера ``#rTag``, ожидание ``#rWait`` и ошибка запроса ``#rErr`` —
над вердиктом, вердикт ``.vbig/.vsub/.vpct/.vwhy``, кнопки ``#rActs``, цифры
``#count/#rFair/#rPpsm``, шкала ``#rRange/.rmk``, путь оценки ``#fxPath``, факторы
``#fxList .fx``, предупреждения ``#rWarn``, история ``#rHist``, аналоги ``#rSim``,
факты ``.rfoot``), живые числа рынка — элементы с ``data-l``.
"""

import json
import re
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
    # Карточка разбора на месте с примером — и честно подписана как пример.
    expect(page.locator(".sheet")).to_be_visible()
    expect(page.locator("#rTag")).to_be_visible()
    expect(page.locator("#rTag")).to_contain_text("Пример")
    expect(page.locator("#count")).to_contain_text("27 500 000")
    # Кнопки «поделиться/следить» у примера не показываем — делиться нечем.
    expect(page.locator("#rActs")).to_be_hidden()
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
    # Вердикт FAIR → «В рынке», плашка нейтральная (золото — только «дороже»).
    expect(sheet.locator(".vbig")).to_have_text("В рынке")
    expect(sheet.locator(".vpct")).to_have_text("+1,8%")
    expect(page.locator(".verdict")).to_have_class("verdict fair")
    # Почему «в рынке»: вердикт по обычному разбросу, а не по проценту.
    expect(page.locator(".vwhy")).to_have_text(
        "Обычный разброс для таких квартир — от −12,2% до +13,3%. Разница +1,8% в него укладывается.")
    # Пример ушёл: плашки нет, рамка обычная.
    expect(page.locator("#rTag")).to_be_hidden()
    expect(sheet).not_to_have_class(re.compile(r"\bis-demo\b"))
    # Цифры: цена объявления, справедливая, цена метра.
    expect(page.locator("#count")).to_contain_text("47 000 000")
    expect(page.locator("#rFair")).to_contain_text("46 190 000")
    expect(page.locator("#rPpsm")).to_contain_text("642 955")  # 47 000 000 / 73.1
    expect(page.locator("#rBand")).to_contain_text("такие квартиры обычно стоят 40,5–52,4 млн")
    expect(page.locator("#rRange")).to_contain_text("40,5–52,4 млн")
    # Две метки на шкале: цена в объявлении и оценка.
    expect(page.locator(".rmk.ask span")).to_have_text("в объявлении · 47 млн")
    expect(page.locator(".rmk.fair span")).to_have_text("оценка · 46,2 млн")
    # Факторы: 5 штук, русские подписи, направление словом.
    factors = page.locator("#fxList .fx")
    expect(factors).to_have_count(5)
    expect(factors.first).to_have_class("fx pos")
    expect(factors.first.locator(".fxn")).to_contain_text("Площадь")
    expect(factors.first.locator(".fxd")).to_contain_text("повышает")
    expect(factors.first.locator(".fxv")).to_have_text("+10 млн")
    # История цены: две точки, продавец снизил цену.
    expect(page.locator("#rHist")).to_be_visible()
    expect(page.locator("#rHist")).to_contain_text("продавец снизил цену")
    # Похожие лоты — ссылки на krisha.kz.
    expect(page.locator("#rSim")).to_be_visible()
    expect(page.locator("#rSim a[href*='krisha.kz/a/show/']")).to_have_count(3)
    # Кнопки сразу под вердиктом: «поделиться» и слежение в боте.
    acts = page.locator("#rActs")
    expect(page.locator(".rshare")).to_have_count(0)
    expect(acts.locator("a[href='https://t.me/fairprice_kzbot?start=track_761891663']")).to_be_visible()
    # Факты об объявлении.
    expect(page.locator(".rfoot")).to_contain_text("похожие снимают с продажи за")
    expect(page.locator(".rfoot")).to_contain_text("в продаже 68 дн.")
    # Источник — ссылка на объявление, и живой статус.
    expect(page.locator("#repSrc")).to_contain_text("krisha.kz/a/show/761891663")
    expect(page.locator("#repSrc")).to_have_attribute("href", LOT_URL)
    # Старый API без factors_base: путь «типичная квартира → оценка» не рисуем.
    expect(page.locator("#fxPath")).to_be_hidden()
    expect(page.locator("#checkStatus")).to_have_text("Готово: В рынке, +1,8%")
    # Предупреждений нет — блок скрыт.
    expect(page.locator("#rWarn")).to_be_hidden()


def test_home_predict_overpriced_verdict_label(hermetic_page, mock_api, hermetic_server, predict_overpriced):
    page = hermetic_page
    mock_api(predict=predict_overpriced)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    expect(page.locator(".vbig")).to_have_text("Дорого")
    expect(page.locator(".vsub")).to_contain_text("есть повод торговаться")
    expect(page.locator(".vpct")).to_have_text("+29,9%")
    expect(page.locator(".verdict")).to_have_class("verdict high")
    expect(page.locator(".vwhy")).to_contain_text("Цена выше даже верхней границы.")


def test_home_predict_good_deal_shows_scam_warning(hermetic_page, mock_api, hermetic_server, predict_good_deal):
    page = hermetic_page
    mock_api(predict=predict_good_deal)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    # Сильно ниже рынка — не зелёное «Выгодно −28,5%», а предупреждение без процента.
    expect(page.locator(".vbig")).to_have_text("Подозрительно дёшево")
    expect(page.locator(".vpct")).to_be_hidden()
    expect(page.locator(".verdict")).to_have_class("verdict alert")
    expect(page.locator("#checkStatus")).to_have_text("Готово: Подозрительно дёшево")
    warn = page.locator("#rWarn")
    expect(warn).to_be_visible()
    expect(warn).to_contain_text("Цена сильно ниже рынка")
    expect(warn).to_contain_text("ниже нижней границы интервала модели")
    expect(warn).to_contain_text("Не вносите задаток")


def test_home_good_deal_without_scam_risk_is_green(hermetic_page, mock_api, hermetic_server, predict_good_deal):
    page = hermetic_page
    data = deepcopy(predict_good_deal)
    data["scam_risk"] = None
    mock_api(predict=data)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    expect(page.locator(".vbig")).to_have_text("Выгодно")
    expect(page.locator(".vpct")).to_have_text("−28,5%")
    expect(page.locator(".verdict")).to_have_class("verdict good")
    expect(page.locator(".vwhy")).to_contain_text("Цена ниже даже нижней границы.")


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
    expect(page.locator(".rmk.fair span")).to_have_text("оценка · 46,2 млн")
    expect(page.locator(".vwhy")).to_be_hidden()
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


def test_home_factors_path_from_typical_flat(hermetic_page, mock_api, hermetic_server, predict_fair):
    """factors_base → путь «типичная квартира → особенности → оценка», остальное — «прочее».
    База + вклады + прочее == оценка, поэтому путь сходится."""
    page = hermetic_page
    data = deepcopy(predict_fair)
    data["factors_base"] = 43_760_000.0
    data["factors_other"] = 46_190_000.0 - 43_760_000.0 - sum(f["impact_tenge"] for f in data["top_factors"])
    mock_api(predict=data)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    path = page.locator("#fxPath")
    expect(path).to_be_visible()
    expect(path.locator(".fxpv")).to_have_text(["43,8 млн ₸", "+2,4 млн ₸", "46,2 млн ₸"])
    expect(page.locator("#fxSub")).to_have_text("по сравнению с типичной квартирой в Алматы")
    expect(page.locator("#fxList .fx")).to_have_count(6)
    other = page.locator("#fxList .fx.oth")
    expect(other.locator(".fxn")).to_contain_text("Прочее")
    expect(other.locator(".fxv")).to_have_text("−5,5 млн")
    # Подписи без дублей: каждая встречается один раз.
    names = page.locator("#fxList .fxn").all_text_contents()
    assert len(names) == len(set(names)), names


def test_home_report_escapes_listing_text(hermetic_page, mock_api, hermetic_server, predict_fair):
    """title/address/url приходят от krisha: разметка из них не исполняется, чужая ссылка не ставится."""
    page = hermetic_page
    data = deepcopy(predict_fair)
    data.update({"title": "<img src=x onerror=window.__xss=1>3-комн", "address": "<b id=evil>Адрес</b>",
                 "url": "javascript:window.__xss=2"})
    mock_api(predict=data)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    expect(page.locator(".rfoot")).to_contain_text("<img src=x")
    assert page.evaluate("document.querySelectorAll('.sheet img, #evil').length") == 0
    assert page.evaluate("window.__xss") is None
    # ссылка собрана из номера объявления, а не взята как есть
    expect(page.locator("#repSrc")).to_have_attribute("href", LOT_URL)


def test_home_server_error_shows_friendly_status(hermetic_page, mock_api, hermetic_server, predict_fair):
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

    # Ошибка — внутри карточки: что случилось и что сделать; пример спрятан.
    err = page.locator("#rErr")
    expect(err).to_be_visible()
    expect(err).to_contain_text("Сервис сейчас не отвечает. Попробуйте ещё раз через минуту")
    expect(err.locator("[data-retry]")).to_be_visible()
    expect(page.locator("#rBody")).to_be_hidden()
    expect(page.locator("#rTag")).to_be_hidden()
    expect(page.locator("#checkStatus")).to_have_text("Не удалось получить оценку")
    # Кнопка разблокирована для повторной попытки, скелетон снят.
    expect(page.locator("form[data-check]:has(#lotUrl) .gobtn")).to_be_enabled()
    expect(page.locator(".sheet")).to_have_attribute("aria-busy", "false")
    expect(page.locator("#rWait")).to_be_hidden()

    # «Попробовать ещё раз» повторяет тот же запрос — теперь удачно.
    page.unroute("**/api/predict")
    mock_api(predict=predict_fair)
    err.locator("[data-retry]").click()
    expect(page.locator(".vbig")).to_have_text("В рынке")
    expect(err).to_be_hidden()
    expect(page.locator("#rBody")).to_be_visible()


def test_home_removed_listing_shows_server_detail(hermetic_page, mock_api, hermetic_server):
    """predict-edge-listings AC-3.1: 404 — объявление сняли; это не «сервис не
    отвечает», фронт показывает текст сервера."""
    page = hermetic_page
    mock_api()
    detail = "Объявление не найдено — возможно, его уже сняли с продажи"
    page.route(
        "**/api/predict",
        lambda r: r.fulfill(
            status=404,
            body=json.dumps({"detail": detail}),
            content_type="application/json",
        ),
    )
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page, "https://krisha.kz/a/show/123456789")

    err = page.locator("#rErr")
    expect(err).to_contain_text(detail)
    expect(err).to_contain_text("Проверьте ссылку или возьмите другое объявление")
    expect(err).not_to_contain_text("Сервис сейчас не отвечает")
    # снятое объявление повтор не исправит — кнопки повтора нет
    expect(err.locator("[data-retry]")).to_be_hidden()


def test_home_unparsable_listing_422_message(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.route("**/api/predict", lambda r: r.fulfill(
        status=422, body=json.dumps({"detail": "bad"}), content_type="application/json"))
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page, "https://krisha.kz/a/show/123456789")

    expect(page.locator("#rErr")).to_contain_text("Такое объявление не открывается — проверьте ссылку")
    expect(page.locator("#lotUrl")).to_have_attribute("aria-invalid", "true")


def test_home_rate_limit_429_message_reaches_user(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    page.route("**/api/predict", lambda r: r.fulfill(
        status=429, body=json.dumps({"detail": "too many"}), content_type="application/json"))
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page, "https://krisha.kz/a/show/123456789")

    expect(page.locator("#rErr")).to_contain_text("Слишком много запросов подряд. Попробуйте через минуту")


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
    expect(btn).to_have_text("Проверяем…")
    expect(btn).to_have_attribute("data-l0", re.compile(r"Проверить"))
    expect(page.locator(".sheet")).to_have_attribute("aria-busy", "true")
    expect(page.locator("#checkStatus")).to_have_text("Считаем оценку объявления")
    # Статус ожидания — над вердиктом, а не в низу высокой карточки.
    wait = page.locator("#rWait")
    expect(wait).to_be_visible()
    expect(wait).to_contain_text("Открываем объявление")
    assert wait.bounding_box()["y"] < page.locator(".verdict").bounding_box()["y"]
    # Подпись site.js под карточкой главной не нужна.
    expect(page.locator(".waitmsg")).to_have_count(0)

    # После ответа всё возвращается в рабочее состояние.
    expect(page.locator(".vbig")).to_have_text("В рынке")
    expect(btn).to_be_enabled()
    expect(page.locator(".sheet")).to_have_attribute("aria-busy", "false")
    expect(wait).to_be_hidden()


def test_home_gives_up_after_45_seconds(hermetic_page, mock_api, hermetic_server):
    """Сервер не ответил за 45 секунд — запрос обрывается, в карточке понятная ошибка."""
    page = hermetic_page
    page.clock.install()
    mock_api()
    page.route("**/api/predict", lambda route: None)  # ответа нет вовсе
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)
    expect(page.locator("#rWait")).to_be_visible()
    page.clock.run_for(46_000)

    err = page.locator("#rErr")
    expect(err).to_contain_text("Оценка не пришла за 45 секунд")
    expect(err.locator("[data-retry]")).to_be_visible()
    expect(page.locator("form[data-check]:has(#lotUrl) .gobtn")).to_be_enabled()


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
    # Само значение не фиксируем — оно переписывается каждым переобучением,
    # а тест должен ловить пустоту, а не расхождение с конкретной неделей.
    expect(page.locator("[data-l=total]").first).to_have_text(re.compile(r"^\d{1,3}(\s\d{3})+$"))
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


def test_home_success_feedback_and_permalink(hermetic_page, mock_api, hermetic_server, predict_fair):
    """После проверки: плашка «Готово», объект оценки в шапке карточки, фокус на нём, #check=<id> в адресе."""
    page = hermetic_page
    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    wait_ready(page)
    submit(page)
    expect(page.locator(".vbig")).to_have_text("В рынке")

    expect(page.locator("#rDone")).to_be_visible()
    expect(page.locator("#rDone")).to_contain_text("Готово — оценка по объявлению")
    expect(page.locator("#rHeadT")).to_contain_text("3-комнатная")
    expect(page.locator("#rHeadT")).not_to_contain_text("В рынке")
    expect(page.locator("#rHeadT")).to_be_focused()
    expect(page.locator("#repDemo")).to_be_hidden()
    assert page.evaluate("location.hash") == "#check=761891663"


def test_home_double_submit_says_already_counting(hermetic_page, mock_api, hermetic_server, predict_fair):
    page = hermetic_page
    mock_api()

    def slow(route):
        page.wait_for_timeout(1200)
        route.fulfill(status=200, body=json.dumps(predict_fair), content_type="application/json")

    page.route("**/api/predict", slow)
    page.goto(hermetic_server + "/")
    wait_ready(page)
    submit(page)
    page.evaluate("document.querySelector('form[data-check]:has(#lotUrl)').requestSubmit()")
    expect(page.locator("[data-wait]").first).to_contain_text("Уже считаем, секунду…")
    expect(page.locator("#rDone")).to_be_visible(timeout=5000)  # дождаться ответа: маршрут не должен пережить тест


def test_home_other_city_shows_server_reason(hermetic_page, mock_api, hermetic_server):
    """Объявление не из Алматы: сервер отвечает 422 с причиной — её и видит человек."""
    page = hermetic_page
    mock_api()
    detail = "Оцениваем только квартиры в Алматы, а это объявление из другого города"
    page.route("**/api/predict", lambda r: r.fulfill(
        status=422, body=json.dumps({"detail": detail}), content_type="application/json"))
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page, "https://krisha.kz/a/show/1013224169")

    err = page.locator("#rErr")
    expect(err).to_contain_text(detail)
    expect(err).to_contain_text("Модели учились на объявлениях Алматы")
    expect(err).not_to_contain_text("проверьте ссылку")
    expect(err.locator("[data-retry]")).to_be_hidden()


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
    # Один ключ bagam-theme; старый kfp-theme больше не пишется (site.js переносит его и удаляет).
    assert page.evaluate("localStorage.getItem('bagam-theme')") == now
    assert page.evaluate("localStorage.getItem('kfp-theme')") is None
    # Подпись переключателя рассказывает текущую тему.
    expect(page.locator("[data-theme-label]").first).to_have_text(
        "светлая" if now == "light" else "тёмная")

    page.reload()
    wait_ready(page)
    assert page.get_attribute("html", "data-theme") == now


def test_theme_follows_system_and_migrates_old_key(hermetic_page, mock_api, hermetic_server):
    page = hermetic_page
    mock_api()
    # без сохранённого выбора — системная тема
    page.emulate_media(color_scheme="light")
    page.goto(hermetic_server + "/about")
    assert page.get_attribute("html", "data-theme") == "light"
    page.emulate_media(color_scheme="dark")
    page.goto(hermetic_server + "/about")
    assert page.get_attribute("html", "data-theme") == "dark"
    # старый ключ переносится в bagam-theme и удаляется
    page.evaluate("localStorage.clear(); localStorage.setItem('kfp-theme', 'light')")
    page.goto(hermetic_server + "/about")
    assert page.get_attribute("html", "data-theme") == "light"
    assert page.evaluate("[localStorage.getItem('bagam-theme'), localStorage.getItem('kfp-theme')]") == ["light", None]


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


def test_home_rent_listing_renders_monthly_report(hermetic_page, mock_api, hermetic_server, predict_rent):
    """Аренда: цены в тыс. ₸/мес, арендные подписи и факторы, без слежения в боте."""
    page = hermetic_page
    mock_api(predict=predict_rent)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    expect(page.locator(".vbig")).to_have_text("В рынке")
    expect(page.locator(".vsub")).to_contain_text("аренда в обычных пределах")
    expect(page.locator(".vpct")).to_have_text("+10,3%")
    expect(page.locator("#count")).to_contain_text("320 000")
    expect(page.locator("#count small")).to_have_text("₸/мес")
    expect(page.locator("#rFair")).to_contain_text("290 000")
    expect(page.locator("#rFair small")).to_have_text("₸/мес")
    expect(page.locator("#rPpsm small")).to_have_text("₸/м² в мес")
    expect(page.locator("#rPpsm + .rsub")).to_be_hidden()  # медиана города — продажная
    expect(page.locator(".rmk.ask span")).to_have_text("в объявлении · 320 тыс")
    expect(page.locator(".rmk.fair span")).to_have_text("оценка · 290 тыс")
    expect(page.locator("#rRange")).to_contain_text("250–340 тыс")
    expect(page.locator("#rBand")).to_contain_text("такие квартиры обычно сдают за 250–340 тыс")
    factors = page.locator("#fxList .fx")
    expect(factors).to_have_count(4)
    expect(factors.nth(1).locator(".fxn")).to_have_text("Кондиционер")
    expect(factors.nth(1).locator(".fxv")).to_have_text("+14 тыс")
    expect(factors.nth(2).locator(".fxn")).to_have_text("Можно с животными")
    expect(factors.nth(3).locator(".fxn")).to_have_text("Арендодатель")
    expect(page.locator("#rHist")).to_contain_text("арендодатель снизил цену на 30 тыс")
    expect(page.locator("#rSim")).to_contain_text("280 тыс")
    foot = page.locator(".rfoot")
    expect(foot).to_contain_text("похожие сдают за")
    expect(foot).to_contain_text("ищут жильца 12 дн.")
    # слежка за арендой работает (сверка после вечернего обхода аренды) — кнопка есть
    expect(page.locator("#rActs a[href*='t.me/fairprice_kzbot?start=track_']")).to_have_count(1)
    expect(page.locator(".sheet")).not_to_contain_text("млн")


def test_home_rent_room_share_has_no_verdict(hermetic_page, mock_api, hermetic_server, predict_rent):
    page = hermetic_page
    data = deepcopy(predict_rent)
    data.update({"room_share": True, "verdict": None, "actual_price": 90_000, "diff_pct": -69.0})
    mock_api(predict=data)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    expect(page.locator(".vbig")).to_have_text("Оценка модели")
    expect(page.locator(".vsub")).to_contain_text("подселение")
    expect(page.locator(".vpct")).to_be_hidden()
    expect(page.locator("#rWarn")).to_be_visible()
    expect(page.locator("#rWarn")).to_contain_text("Похоже на подселение или сдачу комнаты")
    # строка статуса — те же слова, без процента (было «Готово: В рынке, −69,0%»)
    expect(page.locator("#checkStatus")).to_have_text("Готово: Оценка модели")


def test_home_sale_after_rent_restores_sale_units(hermetic_page, mock_api, hermetic_server, predict_rent, predict_fair):
    """Режим аренды не «залипает»: следующий продажный разбор — снова в млн ₸."""
    page = hermetic_page
    mock_api(predict=predict_rent)
    page.goto(hermetic_server + "/")
    wait_ready(page)
    submit(page)
    expect(page.locator("#rFair small")).to_have_text("₸/мес")

    mock_api(predict=predict_fair)
    submit(page, "https://krisha.kz/a/show/761891664")

    expect(page.locator("#rFair")).to_contain_text("46 190 000")
    expect(page.locator("#rFair small")).to_have_text("₸")
    expect(page.locator("#rPpsm + .rsub")).to_be_visible()
    expect(page.locator(".rmk.fair span")).to_contain_text("оценка · 46,2 млн")


def test_home_sale_shows_rental_yield(hermetic_page, mock_api, hermetic_server, predict_fair):
    """Продажа: блок «Если сдавать» — аренда, доходность против района, окупаемость."""
    page = hermetic_page
    data = deepcopy(predict_fair)
    data["rental_yield"] = {
        "monthly_rent": 330_000, "monthly_rent_low": 280_000, "monthly_rent_high": 390_000,
        "gross_yield_pct": 8.4, "gross_yield_low_pct": 7.1, "gross_yield_high_pct": 10.0,
        "payback_years": 11.9, "district_yield_pct": 7.6, "district_yield_scope": "district_rooms",
        "assumes_renovation": False,
    }
    mock_api(predict=data)
    page.goto(hermetic_server + "/")
    wait_ready(page)

    submit(page)

    box = page.locator("#rYield")
    expect(box).to_be_visible()
    expect(box).to_contain_text("Если сдавать эту квартиру")
    expect(box.locator(".ryc").nth(0)).to_contain_text("~330 тыс")
    expect(box.locator(".ryc").nth(0)).to_contain_text("обычно 280–390 тыс")
    expect(box.locator(".ryc").nth(1)).to_contain_text("8,4%")
    expect(box.locator(".ryc").nth(1)).to_contain_text("у таких же квартир района ~7,6%")
    expect(box.locator(".ryv.acc")).to_have_count(1)  # доходность выше районной — акцент
    expect(box.locator(".ryc").nth(2)).to_contain_text("11,9")
    expect(box.locator(".ryc").nth(2)).to_contain_text("года")


def test_home_rent_and_plain_sale_hide_rental_yield(hermetic_page, mock_api, hermetic_server, predict_rent, predict_fair):
    """Доходность — только у продажи и только когда API её посчитал."""
    page = hermetic_page
    data = deepcopy(predict_rent)
    data["rental_yield"] = {"monthly_rent": 300_000, "gross_yield_pct": 9.0}  # аренде не положено
    mock_api(predict=data)
    page.goto(hermetic_server + "/")
    wait_ready(page)
    submit(page)
    expect(page.locator("#rFair small")).to_have_text("₸/мес")
    expect(page.locator("#rYield")).to_be_hidden()

    plain = deepcopy(predict_fair)
    plain["rental_yield"] = None
    mock_api(predict=plain)
    submit(page, "https://krisha.kz/a/show/761891664")
    expect(page.locator("#rFair small")).to_have_text("₸")
    expect(page.locator("#rYield")).to_be_hidden()
