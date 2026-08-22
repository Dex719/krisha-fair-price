"""API-контракт через реальный HTTP-стек (uvicorn + сокеты), не TestClient.

TestClient-тесты (test_api_security.py и др.) гоняют ASGI-приложение в
процессе; здесь проверяется то, что добавляет реальный сервер: парсинг HTTP
uvicorn'ом, порядок middleware на настоящем соединении, chunked-тела,
поведение прокси-заголовков.
"""

import json

import httpx
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def api(hermetic_server):
    with httpx.Client(base_url=hermetic_server, timeout=30.0) as client:
        yield client


def test_health_ok_with_security_headers(api):
    r = api.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_pages_serve_html_with_csp(api):
    for path, marker in (("/", 'id="form"'), ("/stats", 'id="price-map"'), ("/about", "Оценка")):
        r = api.get(path)
        assert r.status_code == 200, path
        assert marker in r.text, path
        assert "content-security-policy" in r.headers, path


def test_predict_invalid_url_is_422_with_friendly_detail(api):
    r = api.post("/api/predict", json={"url": "https://example.com/x"})
    assert r.status_code == 422
    assert "krisha.kz" in str(r.json()["detail"])


def test_predict_oversized_content_length_is_413(api):
    r = api.post(
        "/api/predict",
        content=json.dumps({"url": "x" * (70 * 1024)}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    # Ранний ответ middleware тоже несёт security-заголовки.
    assert "content-security-policy" in r.headers


def test_predict_chunked_body_without_content_length_is_413(api):
    """Transfer-Encoding: chunked против реального uvicorn (issue #113)."""

    def oversized():
        for _ in range(10):
            yield b"x" * 8192  # суммарно 80KB > 64KB

    r = api.post(
        "/api/predict",
        content=oversized(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert r.json()["detail"] == "Слишком большой запрос"


def test_forecast_404_when_flag_off(api):
    r = api.get("/api/forecast")
    assert r.status_code == 404


def test_tg_webhook_404_without_token(api):
    r = api.post("/tg/webhook", json={"update_id": 1})
    assert r.status_code == 404


def test_unknown_path_is_404(api):
    assert api.get("/no-such-page").status_code == 404


def test_docs_and_openapi_available_but_hide_webhook(api):
    r = api.get("/openapi.json")
    assert r.status_code == 200
    assert "/tg/webhook" not in r.json()["paths"]
    assert api.get("/docs").status_code == 200


def test_demo_contract_depends_on_db_presence(api):
    """С базой на диске /api/demo отдаёт живой лот, без неё — честный 503.

    Сервер герметичного профиля не скачивает базу (KRISHA_DB_AUTO=0), но
    локально data/krisha.db обычно уже лежит — обе ветки контрактны.
    """
    from pathlib import Path

    db_present = (Path(__file__).resolve().parents[2] / "data" / "krisha.db").exists()
    r = api.get("/api/demo")
    if db_present:
        assert r.status_code == 200
        assert "krisha.kz/a/show/" in r.json()["url"]
    else:
        assert r.status_code == 503


def test_rate_limit_429_after_burst_over_real_socket(api):
    """15 запросов проходят, 16-й — 429 (сам лимитер работает на живом сокете)."""
    codes = []
    for _ in range(20):
        codes.append(api.get("/api/demo").status_code)
        if codes[-1] == 429:
            break
    assert 429 in codes, f"после {len(codes)} запросов 429 не наступил: {codes}"
    # /api/demo без базы отвечает 503, но rate-limit срабатывает ДО обращения
    # к базе — важна сама смена 503 → 429.
    assert codes[-1] == 429


def test_rate_limit_not_bypassed_by_rotating_left_xff(api):
    """Обход закрыт на реальном HTTP-стеке: смена ЛЕВОГО элемента XFF на каждом
    запросе не даёт нового бакета, когда правый (от «доверенного прокси») один.

    Именно так обходился прод HF: uvicorn берёт крайний левый XFF и кладёт в
    request.client, поэтому фикс обязан читать правый элемент из заголовка
    сам — что и проверяется здесь через настоящий uvicorn, а не TestClient.
    """
    # Пауза на сброс окна rate-limit от предыдущего теста (RATE_WINDOW_S=60)
    # не нужна: правый IP тут другой (198.51.100.7) — это отдельный бакет.
    codes = []
    for i in range(20):
        r = api.get("/api/demo", headers={"x-forwarded-for": f"9.9.9.{i}, 198.51.100.7"})
        codes.append(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in codes, f"меняющийся левый XFF обошёл лимит: {codes}"
    assert codes.index(429) == 15, f"429 ожидался на 16-м запросе, коды: {codes}"
