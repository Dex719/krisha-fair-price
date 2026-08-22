"""Фикстуры браузерных e2e-тестов (pytest-playwright).

Два режима, разделённые маркерами:

* ``@pytest.mark.e2e`` — герметичные тесты. Поднимают локальный uvicorn БЕЗ
  базы (KRISHA_DB_AUTO=0), а все ``/api/*`` и внешние ресурсы (Telegram SDK,
  Leaflet CDN, тайлы карты, фото krisha) перехватываются Playwright-роутами и
  отдаются из JSON-фикстур. Ни одного живого запроса в сеть — гоняются в CI
  и оффлайн, детерминированы.

* ``@pytest.mark.live`` — smoke поверх реального сервера с реальной базой и
  живым походом на krisha.kz (кнопка «Показать на примере»). Не запускаются
  в дефолтном прогоне и в CI; включаются вручную: ``pytest -m live``.

Оба маркера исключены из обычного ``pytest -q`` через ``addopts`` в
pyproject.toml, поэтому ``make test`` / CI-джоб юнитов их не трогают.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"


# --- фикстуры-данные -------------------------------------------------------
def _load(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def stats_data() -> dict:
    return _load("stats.json")


@pytest.fixture(scope="session")
def heatmap_data() -> list:
    return _load("heatmap.json")


@pytest.fixture(scope="session")
def health_data() -> dict:
    """Ответ /api/health для герметичных тестов.

    Настоящий сервер отвечает по состоянию машины (есть база — есть возраст
    данных, нет — null), поэтому страницы с живыми числами мокаются и здесь:
    иначе тест зелёный локально и красный в CI, где базы нет.
    """
    return {
        "status": "ok",
        "model_loaded": True,
        "model_error_pct": 7.6,
        "model_median_error_pct": 5.1,
        "data_age_hours": 3.0,
        "freshness": "fresh",
    }


@pytest.fixture(scope="session")
def predict_fair() -> dict:
    """Реальный ответ /api/predict (вердикт FAIR) — база всех вариантов ниже."""
    return _load("predict_fair.json")


@pytest.fixture(scope="session")
def predict_overpriced(predict_fair) -> dict:
    """Тот же лот, но цена задрана — вердикт OVERPRICED, diff_pct > +10%."""
    data = deepcopy(predict_fair)
    data["actual_price"] = 60_000_000
    data["verdict"] = "OVERPRICED"
    data["diff_pct"] = 29.9
    return data


@pytest.fixture(scope="session")
def predict_good_deal(predict_fair) -> dict:
    """Цена заметно ниже интервала — вердикт GOOD_DEAL + бейдж риска."""
    data = deepcopy(predict_fair)
    data["actual_price"] = 33_000_000
    data["verdict"] = "GOOD_DEAL"
    data["diff_pct"] = -28.5
    data["scam_risk"] = {
        "level": "high",
        "below_pct": 18.6,
        "reasons": ["Цена на 18.6% ниже нижней границы интервала модели"],
    }
    return data


@pytest.fixture(scope="session")
def predict_no_price(predict_fair) -> dict:
    """Объявление без цены — карточка показывает оценку модели, без вердикта."""
    data = deepcopy(predict_fair)
    data["actual_price"] = None
    data["verdict"] = None
    data["diff_pct"] = None
    data["price_history"] = []
    return data


# --- локальный сервер ------------------------------------------------------
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(base_url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/health", timeout=5.0)
            if r.status_code == 200:
                return
        except httpx.HTTPError as exc:  # сервер ещё поднимается
            last_err = exc
        time.sleep(0.4)
    raise RuntimeError(f"сервер не поднялся за {timeout}s: {last_err}")


def _start_server(hermetic: bool) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    env = os.environ.copy()
    if hermetic:
        # без похода в GitHub Release за базой/моделями на старте: фронт всё
        # равно кормится роутами-моками, база серверу для отдачи статики не нужна.
        env["KRISHA_DB_AUTO"] = "0"
        env["KRISHA_MODEL_AUTO"] = "0"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "krisha.api.app:app", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_health(base_url)
    except Exception:
        proc.terminate()
        raise
    return proc, base_url


@pytest.fixture(scope="session")
def hermetic_server() -> str:
    """uvicorn без базы для герметичных тестов. Данные приходят из роутов-моков."""
    proc, base_url = _start_server(hermetic=True)
    yield base_url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def live_server() -> str:
    """uvicorn с реальной базой/моделями и живым krisha.kz — только для -m live."""
    if not (ROOT / "data" / "krisha.db").exists():
        pytest.skip("нет data/krisha.db — live-тесты требуют реальную базу")
    proc, base_url = _start_server(hermetic=False)
    yield base_url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# --- герметичный мокинг сети ----------------------------------------------
# Пустые заглушки для внешних ресурсов, чтобы страница не ходила в интернет.
_STUB_JS = "window.__stubbed_js=true;"
_STUB_CSS = "/* stubbed */"
_TRANSPARENT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da63fcffff3f0300050001ff9a9c2c0000000049454e44ae426082"
)


def _fulfill(route, body, content_type):
    route.fulfill(status=200, body=body, content_type=content_type)


@pytest.fixture
def hermetic_page(page, hermetic_server, stats_data, heatmap_data):
    """Playwright-страница с перехватом внешних хостов.

    ``/api/*`` намеренно НЕ трогаем здесь — каждый тест сам ставит нужные
    ответы через ``mock_api`` (или полагается на дефолт ниже для stats/heatmap).
    Внешние ресурсы (Telegram SDK, Leaflet, тайлы, фото) заглушаются всегда.
    """
    def _external(route):
        url = route.request.url
        if url.endswith(".css"):
            _fulfill(route, _STUB_CSS, "text/css")
        elif url.endswith(".js"):
            _fulfill(route, _STUB_JS, "application/javascript")
        else:
            _fulfill(route, _TRANSPARENT_PNG, "image/png")

    for pattern in (
        "https://telegram.org/**",
        "https://cdn.jsdelivr.net/**",
        "https://*.basemaps.cartocdn.com/**",
        "https://*.kcdn.online/**",
    ):
        page.route(pattern, _external)

    page.set_default_timeout(15_000)
    return page


@pytest.fixture
def mock_api(hermetic_page, stats_data, heatmap_data, health_data):
    """Хелпер: ставит JSON-ответы на ``/api/*`` для текущей страницы.

    Использование::

        mock_api(predict=predict_fair)
        page.goto(...)
    """
    page = hermetic_page

    def _install(predict: dict | None = None,
                 stats: dict | None = None,
                 heatmap: list | None = None,
                 health: dict | None = None,
                 demo_url: str = "https://krisha.kz/a/show/761891663",
                 forecast_status: int = 404):
        stats = stats if stats is not None else stats_data
        heatmap = heatmap if heatmap is not None else heatmap_data
        health = health if health is not None else health_data

        page.route("**/api/stats", lambda r: _fulfill(
            r, json.dumps(stats), "application/json"))
        page.route("**/api/health", lambda r: _fulfill(
            r, json.dumps(health), "application/json"))
        page.route("**/api/heatmap", lambda r: _fulfill(
            r, json.dumps(heatmap), "application/json"))
        page.route("**/api/demo", lambda r: _fulfill(
            r, json.dumps({"listing_id": 761891663, "url": demo_url}), "application/json"))

        def _forecast(route):
            if forecast_status == 404:
                route.fulfill(status=404, body=json.dumps({"detail": "off"}),
                              content_type="application/json")
            else:
                route.fulfill(status=200, body=json.dumps({"city": {}, "districts": []}),
                              content_type="application/json")
        page.route("**/api/forecast", _forecast)

        if predict is not None:
            page.route("**/api/predict", lambda r: _fulfill(
                r, json.dumps(predict), "application/json"))

    return _install
