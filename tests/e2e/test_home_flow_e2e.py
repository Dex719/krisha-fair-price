"""Браузерные e2e главной: история «назад/вперёд», 502, тихий повтор примера, гонка кнопки
«пример» с ручной отправкой и плашка несвежих данных (аудит 2026-10-24). Герметично, см. conftest.py."""

import json
import re
from copy import deepcopy

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

A, B = "761891663", "761891664"
URL = "https://krisha.kz/a/show/"
FORM = "form[data-check]:has(#lotUrl)"


def _ready(page):
    expect(page.locator("[data-l=total]").first).to_have_text("18 680")


def _submit(page, lot_id):
    page.fill("#lotUrl", URL + lot_id)
    page.click(FORM + " .gobtn")


def _json(route, data, status=200):
    route.fulfill(status=status, body=json.dumps(data), content_type="application/json")


def _two_lots(predict_fair):
    """Два разных разбора: у B своя цена и заголовок — карточку не спутать."""
    a = deepcopy(predict_fair)
    b = deepcopy(predict_fair)
    b.update({"title": "1-комнатная квартира · 40 м² · 2/9 этаж", "actual_price": 30_000_000,
              "url": URL + B, "listing_id": int(B)})
    return {A: a, B: b}


def _predict_by_id(page, lots, calls):
    def handler(route):
        lot_id = re.search(r"(\d+)$", route.request.post_data_json["url"]).group(1)
        calls.append(lot_id)
        _json(route, lots[lot_id])
    page.route("**/api/predict", handler)


def test_history_back_forward_renders_from_memory(hermetic_page, mock_api, hermetic_server, predict_fair):
    """«Назад/Вперёд» по #check=<id> показывает прошлые разборы из памяти — без нового /api/predict."""
    page = hermetic_page
    mock_api()
    calls = []
    _predict_by_id(page, _two_lots(predict_fair), calls)
    page.goto(hermetic_server + "/")
    _ready(page)

    _submit(page, A)
    expect(page.locator("#count")).to_contain_text("47 000 000")
    _submit(page, B)
    expect(page.locator("#count")).to_contain_text("30 000 000")
    assert calls == [A, B]

    page.go_back()
    assert page.evaluate("location.hash") == "#check=" + A
    expect(page.locator("#count")).to_contain_text("47 000 000")
    expect(page.locator("#rHeadT")).to_contain_text("3-комнатная")
    expect(page.locator("#lotUrl")).to_have_value(URL + A)
    expect(page.locator("#checkStatus")).to_have_text(re.compile(r"^Готово: В рынке"))

    page.go_forward()
    assert page.evaluate("location.hash") == "#check=" + B
    expect(page.locator("#count")).to_contain_text("30 000 000")
    expect(page.locator("#rHeadT")).to_contain_text("1-комнатная")

    # до первой проверки — пустая карточка с приглашением, а не устаревший разбор
    page.go_back()
    page.go_back()
    assert page.evaluate("location.hash") == ""
    expect(page.locator("#rTag")).to_be_visible()
    expect(page.locator("#rTag")).to_contain_text("Вставьте ссылку")
    expect(page.locator("#rBody")).to_be_hidden()

    page.go_forward()
    expect(page.locator("#count")).to_contain_text("47 000 000")
    assert calls == [A, B], f"история пересчитывала разборы: {calls}"


def test_history_during_calculation_keeps_address_and_card_in_sync(
        hermetic_page, mock_api, hermetic_server, predict_fair):
    """«Назад», пока считается новая ссылка: расчёт обрывается, карточка — та, что в адресе,
    и опоздавший ответ её не перерисовывает и адрес не меняет."""
    page = hermetic_page
    mock_api()
    lots = _two_lots(predict_fair)
    held = []

    def handler(route):
        lot_id = re.search(r"(\d+)$", route.request.post_data_json["url"]).group(1)
        if lot_id == B:
            held.append(route)
        else:
            _json(route, lots[lot_id])
    page.route("**/api/predict", handler)
    page.goto(hermetic_server + "/")
    _ready(page)

    _submit(page, A)
    expect(page.locator("#count")).to_contain_text("47 000 000")
    # «#razbor» — обычная запись истории без разбора: на неё и вернёмся «назад»
    page.evaluate("history.pushState(null, '', '#razbor')")
    page.evaluate(f"history.pushState(null, '', '#check={A}')")
    _submit(page, B)
    expect(page.locator(".sheet")).to_have_attribute("aria-busy", "true")
    page.wait_for_timeout(300)
    assert len(held) == 1

    page.go_back()  # → #razbor: не разбор, расчёт B продолжается
    expect(page.locator(".sheet")).to_have_attribute("aria-busy", "true")
    page.go_forward()  # → #check=A, пока B ещё считается
    assert page.evaluate("location.hash") == "#check=" + A
    expect(page.locator(".sheet")).to_have_attribute("aria-busy", "false")
    expect(page.locator(FORM + " .gobtn")).to_be_enabled()
    expect(page.locator("#count")).to_contain_text("47 000 000")
    expect(page.locator("#rDone")).to_be_visible()

    held[0].fulfill(status=200, body=json.dumps(lots[B]), content_type="application/json")
    page.wait_for_timeout(500)
    expect(page.locator("#count")).to_contain_text("47 000 000")
    assert page.evaluate("location.hash") == "#check=" + A
    expect(page.locator("#rErr")).to_be_hidden()


def test_cloudflare_502_offers_retry(hermetic_page, mock_api, hermetic_server, predict_fair):
    """502 без JSON (страница Cloudflare, пока Space перезапускается) — нейтральный текст и повтор."""
    page = hermetic_page
    mock_api()
    page.route("**/api/predict", lambda r: r.fulfill(
        status=502, body="<html><body>Bad gateway</body></html>", content_type="text/html"))
    page.goto(hermetic_server + "/")
    _ready(page)
    _submit(page, A)

    err = page.locator("#rErr")
    expect(err).to_contain_text("Сервис сейчас не отвечает")
    expect(err).not_to_contain_text("Не получилось разобрать")
    retry = err.locator("[data-retry]")
    expect(retry).to_be_visible()
    expect(retry).to_be_enabled()

    page.unroute("**/api/predict")
    mock_api(predict=predict_fair)
    retry.click()
    expect(page.locator("#rDone")).to_be_visible()
    expect(page.locator(".vbig")).to_have_text("В рынке")
    expect(err).to_be_hidden()
    assert page.evaluate("location.hash") == "#check=" + A


def test_demo_404_then_no_new_example_restores_demo_card(hermetic_page, mock_api, hermetic_server):
    """Пример сняли с krisha (404), а другой подобрать не вышло: карточка снова помечена
    как пример, статус не висит на «Считаем», человеку — подсказка вставить свою ссылку."""
    page = hermetic_page
    mock_api()
    demo_calls = []

    def demo(route):
        demo_calls.append(1)
        if len(demo_calls) == 1:
            _json(route, {"listing_id": int(A), "url": URL + A})
        else:
            _json(route, {"detail": "нет базы"}, status=503)
    page.route("**/api/demo", demo)
    page.route("**/api/predict", lambda r: _json(r, {"detail": "Объявление не найдено"}, status=404))
    page.goto(hermetic_server + "/")
    _ready(page)

    page.click("[data-demo]")

    err = page.locator(FORM + " + [data-err]")
    expect(err).to_have_text("Не получилось подобрать пример. Вставьте свою ссылку")
    expect(page.locator("#rTag")).to_be_visible()
    expect(page.locator("#rTag")).to_contain_text("Пример")
    expect(page.locator("#repDemo")).to_be_visible()
    expect(page.locator(".sheet")).to_have_class(re.compile(r"\bis-demo\b"))
    expect(page.locator("#rBody")).to_be_visible()
    expect(page.locator("#rErr")).to_be_hidden()
    expect(page.locator("#checkStatus")).to_have_text("")
    expect(page.locator(".sheet")).to_have_attribute("aria-busy", "false")
    expect(page.locator(FORM + " .gobtn")).to_be_enabled()


def test_demo_404_silently_takes_another_example(hermetic_page, mock_api, hermetic_server, predict_fair):
    """Первый пример сняли (404) — без ошибки на экране берём следующий и показываем его разбор."""
    page = hermetic_page
    mock_api()
    page.route("**/api/predict", lambda r: (
        _json(r, {"detail": "Объявление не найдено"}, status=404) if r.request.post_data_json["url"].endswith(A)
        else _json(r, predict_fair)))
    first = []

    def demo(route):
        first.append(1)
        _json(route, {"listing_id": int(A if len(first) == 1 else B), "url": URL + (A if len(first) == 1 else B)})
    page.route("**/api/demo", demo)
    page.goto(hermetic_server + "/")
    _ready(page)

    page.click("[data-demo]")

    expect(page.locator("#rDone")).to_be_visible()
    expect(page.locator(".vbig")).to_have_text("В рынке")
    expect(page.locator("#lotUrl")).to_have_value(URL + B)
    assert page.evaluate("location.hash") == "#check=" + B
    expect(page.locator("#rErr")).to_be_hidden()
    expect(page.locator("#checkStatus")).to_have_text(re.compile(r"^Готово"))


def test_demo_answer_does_not_overwrite_own_link(hermetic_page, mock_api, hermetic_server, predict_fair):
    """Нажал «пример», пока /api/demo думает, и отправил свою ссылку: поздний пример не
    затирает ни поле, ни разбор и сам не запускает расчёт."""
    page = hermetic_page
    mock_api()
    held = []
    page.route("**/api/demo", lambda r: held.append(r))  # примеры «думают», пока не отпустим
    calls = []
    lots = _two_lots(predict_fair)
    _predict_by_id(page, lots, calls)
    page.goto(hermetic_server + "/")
    _ready(page)

    demo = page.locator("[data-demo]")
    demo.click()
    expect(demo).to_have_text("Ищем пример…")
    _submit(page, B)
    expect(page.locator("#count")).to_contain_text("30 000 000")

    for r in list(held):
        _json(r, {"listing_id": int(A), "url": URL + A})
    expect(demo).to_have_text("пример продажи")
    page.wait_for_timeout(500)
    expect(page.locator("#lotUrl")).to_have_value(URL + B)
    expect(page.locator("#count")).to_contain_text("30 000 000")
    assert calls == [B], calls
    assert page.evaluate("location.hash") == "#check=" + B


@pytest.mark.parametrize("hours,text", [
    (33.4, "Объявления обновлялись 33 ч назад"),
    (47.0, "Объявления обновлялись 47 ч назад"),
    (80.0, "Объявления обновлялись 3 дня назад"),
])
def test_stale_data_banner(hermetic_page, mock_api, hermetic_server, health_data, predict_fair, hours, text):
    """Данные старше 30 ч (freshness=stale): плашка под формой и предупреждение в карточке.
    До двух суток — часами, а не «давно» и не округлённые «2 дня» при 36 ч."""
    page = hermetic_page
    mock_api(health={**health_data, "freshness": "stale", "data_age_hours": hours}, predict=predict_fair)
    page.goto(hermetic_server + "/")
    _ready(page)

    stale = page.locator("#hStale")
    expect(stale).to_be_visible()
    expect(stale).to_have_text(text + " — оценка может отставать от сегодняшних цен.")
    _submit(page, A)
    expect(page.locator("#rWarn")).to_be_visible()
    expect(page.locator("#rWarn")).to_contain_text("Данные рынка устарели")
    expect(page.locator("#rWarn")).to_contain_text(text)


def test_fresh_data_has_no_stale_banner(hermetic_page, mock_api, hermetic_server, predict_fair):
    page = hermetic_page
    mock_api(predict=predict_fair)
    page.goto(hermetic_server + "/")
    _ready(page)
    expect(page.locator("#hStale")).to_be_hidden()
    _submit(page, A)
    expect(page.locator(".vbig")).to_have_text("В рынке")
    expect(page.locator("#rWarn")).to_be_hidden()
