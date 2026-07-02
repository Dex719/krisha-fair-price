"""Security-заголовки API: CSP, nosniff, Referrer-Policy на всех ответах."""

from fastapi.testclient import TestClient

from krisha.api.app import app


def test_security_headers_present():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'self' https://huggingface.co" in csp
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
