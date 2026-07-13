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


def test_forwarded_for_ignored_unless_proxy_is_trusted(monkeypatch):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/demo",
        "headers": [(b"x-forwarded-for", b"203.0.113.10")],
        "client": ("198.51.100.5", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    }
    request = Request(scope)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    assert app_module._client_ip(request) == "198.51.100.5"
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    assert app_module._client_ip(request) == "203.0.113.10"


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
