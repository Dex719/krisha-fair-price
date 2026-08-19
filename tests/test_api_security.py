"""Security-заголовки API: CSP, nosniff, Referrer-Policy на всех ответах.

Отдельно проверяем, что заголовки навешиваются и на РАННИЕ ответы
middleware (413 при слишком большом теле, 400 при битом Content-Length),
а не только на обычный путь через call_next — раньше _security_headers
возвращал эти ответы до блока с заголовками (замечание из ревью PR #63,
issue #68).
"""

from fastapi.testclient import TestClient

from krisha.api import app as app_module
from krisha.api.app import MAX_BODY_BYTES, app


def _assert_security_headers(resp):
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'self' https://huggingface.co" in csp
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def _request_with_xff(xff: str | None, client: str = "198.51.100.5"):
    from starlette.requests import Request

    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/demo",
            "headers": headers,
            "client": (client, 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_client_ip_uses_proxy_written_rightmost_xff(monkeypatch):
    """Берём IP, вписанный доверенным прокси (правый элемент XFF), а не
    подконтрольный клиенту левый — иначе rate-limit обходится подделкой
    заголовка (проверено на проде HF, см. _client_ip)."""
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)  # дефолт 1 (HF/Railway)

    # Клиент подделал левый элемент, прокси дописал реальный IP справа.
    assert app_module._client_ip(_request_with_xff("6.6.6.6, 203.0.113.10")) == "203.0.113.10"
    # Один элемент (прокси не аппендил) — правый = единственный.
    assert app_module._client_ip(_request_with_xff("203.0.113.10")) == "203.0.113.10"
    # Нет XFF → падаем на request.client.
    assert app_module._client_ip(_request_with_xff(None)) == "198.51.100.5"
    # Мусор в правом элементе → тоже fallback на client, а не крэш.
    assert app_module._client_ip(_request_with_xff("not-an-ip")) == "198.51.100.5"


def test_client_ip_zero_hops_ignores_xff(monkeypatch):
    """TRUSTED_PROXY_HOPS=0 — приложение доступно напрямую, XFF не доверяем."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
    assert app_module._client_ip(_request_with_xff("203.0.113.10")) == "198.51.100.5"


def test_client_ip_two_hops_picks_second_from_right(monkeypatch):
    """Два доверенных прокси → берём второй справа (первый вписал внешний LB)."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    xff = "6.6.6.6, 203.0.113.10, 10.0.0.1"
    assert app_module._client_ip(_request_with_xff(xff)) == "203.0.113.10"


def test_rate_limit_not_bypassed_by_spoofed_left_xff(monkeypatch):
    """Регрессия обхода: смена крайнего ЛЕВОГО XFF не даёт нового бакета.

    Модель HF: клиент управляет левыми элементами, доверенный прокси всегда
    дописывает один и тот же реальный IP справа. Раньше ключ брался слева —
    каждый запрос попадал в свой бакет, и лимит обходился одной строкой."""
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)  # дефолт 1
    app_module._rate.clear()
    client = TestClient(app)

    for i in range(app_module.RATE_LIMIT):
        r = client.get("/api/demo", headers={"x-forwarded-for": f"9.9.9.{i}, 203.0.113.200"})
        # demo без базы = 503, но _check_rate_limit срабатывает ДО обращения к БД
        assert r.status_code in (200, 503)
    blocked = client.get("/api/demo", headers={"x-forwarded-for": "9.9.9.250, 203.0.113.200"})
    assert blocked.status_code == 429


def test_security_headers_present():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    _assert_security_headers(resp)


def test_security_headers_present_on_early_413_oversized_body():
    """Тело больше MAX_BODY_BYTES -> ранний 413 ДО call_next, заголовки всё равно есть."""
    client = TestClient(app)
    resp = client.post(
        "/api/predict",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413
    _assert_security_headers(resp)


def test_security_headers_present_on_early_400_malformed_content_length():
    """Content-Length не парсится как int -> ранний 400 ДО call_next, заголовки всё равно есть."""
    client = TestClient(app)
    resp = client.post(
        "/api/predict",
        content=b"{}",
        headers={"content-length": "not-a-number"},
    )
    assert resp.status_code == 400
    _assert_security_headers(resp)


def _run_chunked_middleware(chunks, max_bytes):
    """Прогоняет _ChunkedBodyLimitMiddleware напрямую на ASGI-уровне с телом,
    приходящим несколькими сообщениями (как при Transfer-Encoding: chunked —
    без единого Content-Length, который видела бы _security_headers)."""
    import asyncio

    async def inner_app(scope, receive, send):
        # Наивный хендлер, читающий тело целиком (как FastAPI под капотом).
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    mw = app_module._ChunkedBodyLimitMiddleware(inner_app, max_bytes=max_bytes)

    messages = list(chunks)
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/predict", "headers": []}
    asyncio.run(mw(scope, receive, send))
    return sent


def test_chunked_body_without_content_length_over_limit_is_rejected():
    """issue #113: Transfer-Encoding: chunked без Content-Length раньше обходил
    64KB-лимит целиком (проверка была только по заголовку) — теперь стриминговый
    подсчёт байт ловит его независимо от заголовков."""
    chunk = b"x" * 40
    sent = _run_chunked_middleware(
        [
            {"type": "http.request", "body": chunk, "more_body": True},
            {"type": "http.request", "body": chunk, "more_body": True},
            {"type": "http.request", "body": chunk, "more_body": False},  # 120 > 100
        ],
        max_bytes=100,
    )
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    headers = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("content-security-policy", "").startswith("default-src 'self'")


def test_chunked_body_without_content_length_under_limit_passes_through():
    chunk = b"x" * 30
    sent = _run_chunked_middleware(
        [
            {"type": "http.request", "body": chunk, "more_body": True},
            {"type": "http.request", "body": chunk, "more_body": False},  # 60 <= 100
        ],
        max_bytes=100,
    )
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 200


def test_rate_limit_survives_concurrent_access(monkeypatch):
    """issue #113: _check_rate_limit мутирует общий словарь (del при вычистке,
    вставка нового ключа через defaultdict) — без блокировки конкурентные
    потоки threadpool'а могут словить RuntimeError на итерации словаря."""
    import concurrent.futures

    from starlette.requests import Request

    monkeypatch.setattr(app_module, "MAX_RATE_KEYS", 5)  # форсируем путь вычистки
    app_module._rate.clear()

    def make_request(ip: str) -> Request:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/predict",
            "headers": [],
            "client": (ip, 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
        return Request(scope)

    errors = []

    def worker(i):
        try:
            for _ in range(200):
                app_module._check_rate_limit(make_request(f"10.0.0.{i % 50}"))
        except app_module.HTTPException:
            pass  # 429 ожидаемо при повторных запросах с одного IP
        except Exception as exc:  # noqa: BLE001 — именно это ловим (регрессия)
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(worker, range(64)))

    assert errors == []


def test_chunked_overflow_returns_413_through_the_real_app():
    """Регрессия: 413-ветка была недостижима в настоящем приложении.

    _MaxBodySizeExceeded бросалось внутри стека FastAPI (при чтении тела), а
    ловилось снаружи, вокруг self.app(...) — но FastAPI перехватывает ошибки
    чтения тела сам и отвечает 400, так что до нашего except исключение уже
    не долетало. Юнит-тесты гоняли middleware в изоляции с заглушкой вместо
    приложения и этого не видели. Здесь бьём по реальному стеку."""
    client = TestClient(app, raise_server_exceptions=False)

    def oversized_body():
        for _ in range(4):
            yield b"x" * (MAX_BODY_BYTES // 2)  # без Content-Length → chunked

    resp = client.post(
        "/api/predict", content=oversized_body(), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413
    assert resp.json()["detail"] == "Слишком большой запрос"
    _assert_security_headers(resp)


def test_webhook_secret_with_non_ascii_header_is_403_not_500(monkeypatch):
    """Регрессия: hmac.compare_digest на двух str требует ASCII-only в обоих
    аргументах, иначе TypeError. Заголовок приходит от кого угодно, поэтому
    подделка с кириллицей давала 500 (и стектрейс в логи) вместо 403."""
    monkeypatch.setattr(app_module.bot, "bot_token", lambda: "test-token")
    client = TestClient(app, raise_server_exceptions=False)

    # Передаём сырые байты: Starlette декодирует заголовки как latin-1, так
    # что 0xE9 приходит в хендлер как не-ASCII str — ровно тот вход, на
    # котором compare_digest(str, str) падал с TypeError.
    resp = client.post(
        "/tg/webhook",
        json={"update_id": 1},
        headers={b"X-Telegram-Bot-Api-Secret-Token": b"\xe9\xe9\xe9-not-ascii"},
    )
    assert resp.status_code == 403


def test_internal_value_error_does_not_leak_as_422(monkeypatch):
    """Регрессия: `except ValueError` ловил всё подряд, поэтому внутренний
    сбой (например JSONDecodeError на битом model_meta.json — подкласс
    ValueError) уезжал пользователю как 422 с сырым текстом исключения."""
    import json as _json

    def boom(*_a, **_kw):
        raise _json.JSONDecodeError("Expecting value", '{"features": <мусор>}', 12)

    monkeypatch.setattr(app_module, "predict_from_url", boom)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/api/predict", json={"url": "https://krisha.kz/a/show/1"})
    assert resp.status_code == 502
    assert "мусор" not in resp.text, "внутренние детали не должны утекать наружу"
