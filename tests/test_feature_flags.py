"""issue #157: фича-флаги для непроверенных фич.

За флагом то, чей вклад в качество оценки не измерен. Дефолт — выключено:
непроверенная фича на пользовательском пути стоит денег (поход в Gemini на
каждый предикт), времени ответа и доверия — если она врёт, врёт вся карточка.
"""

import pytest
from fastapi.testclient import TestClient

from krisha.api.app import app
from krisha.config import (
    FEATURE_FORECAST_ENV,
    FEATURE_VISION_ENV,
    feature_forecast,
    feature_vision,
)


@pytest.fixture(autouse=True)
def _clear_forecast_cache():
    """Кэш прогноза живёт в модуле и переживает смену флага между тестами."""
    from krisha.api import app as app_module

    app_module._forecast_cache.update(data=None, ts=0.0)
    yield
    app_module._forecast_cache.update(data=None, ts=0.0)


def test_flags_are_off_by_default(monkeypatch):
    monkeypatch.delenv(FEATURE_VISION_ENV, raising=False)
    monkeypatch.delenv(FEATURE_FORECAST_ENV, raising=False)

    assert feature_vision() is False
    assert feature_forecast() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_truthy_values_enable(monkeypatch, value):
    monkeypatch.setenv(FEATURE_VISION_ENV, value)
    assert feature_vision() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "off", "нет"])
def test_everything_else_stays_off(monkeypatch, value):
    """Ошибиться должно в сторону «выключено».

    Флаг читают руками в переменных окружения Space; опечатка обязана
    оставить фичу выключенной, а не включить её молча.
    """
    monkeypatch.setenv(FEATURE_VISION_ENV, value)
    assert feature_vision() is False


def test_forecast_endpoint_is_404_when_disabled(monkeypatch):
    """Прогноз выключен → честный 404, а не пустой ответ или 500.

    404 позволяет фронту убрать секцию целиком, а прод-смоуку — отличить
    «фича отключена» от «фича сломалась».
    """
    monkeypatch.delenv(FEATURE_FORECAST_ENV, raising=False)

    with TestClient(app) as client:
        resp = client.get("/api/forecast")

    assert resp.status_code == 404


def test_forecast_endpoint_answers_when_enabled(monkeypatch):
    """Обратная сторона: с включённым флагом эндпоинт снова работает.

    Без этой проверки флаг мог бы намертво закрыть фичу — и мы бы узнали об
    этом, только когда решили её вернуть.
    """
    monkeypatch.setenv(FEATURE_FORECAST_ENV, "1")
    monkeypatch.setattr(
        "krisha.forecast.build_forecast",
        lambda: {"city": {"current_ppsm": 750_000}, "districts": []},
    )

    with TestClient(app) as client:
        resp = client.get("/api/forecast")

    assert resp.status_code == 200
    assert resp.json()["city"]["current_ppsm"] == 750_000


def test_vision_call_is_guarded_by_the_flag():
    """Выключенный флаг обязан означать, что вызова НЕТ, а не что его
    результат отброшен: вся цена фичи — в самом походе в Gemini Vision,
    прямо на пользовательском пути.

    Проверка структурная (по исходнику), а не сквозная: сквозной предикт
    требует обученной модели, которой в CI нет — она качается из релиза.
    Зато эта проверка падает ровно тогда, когда нужно: если кто-то вынесет
    вызов из-под условия или уберёт условие совсем.
    """
    import inspect

    from krisha.predict import _predict_from_listing

    src = inspect.getsource(_predict_from_listing)
    guard_at = src.find("if feature_vision():")
    call_at = src.find("assess_renovation(")
    import_at = src.find("from krisha.vision import")

    assert guard_at != -1, "гард feature_vision() пропал из пути предикта"
    assert call_at > guard_at, "вызов vision должен быть ПОД гардом"
    assert import_at > guard_at, (
        "импорт krisha.vision тоже должен быть под гардом — иначе модуль "
        "тянется даже с выключенной фичей"
    )
