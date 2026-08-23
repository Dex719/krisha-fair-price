"""Smoke-prod script tests: HTTP is mocked, no network calls."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE_PROD_PATH = ROOT / "scripts" / "smoke_prod.py"
spec = importlib.util.spec_from_file_location("smoke_prod", SMOKE_PROD_PATH)
assert spec is not None
assert spec.loader is not None
smoke_prod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke_prod)


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


class FakeClient:
    def __init__(self, get_map, post_map=None):
        self.get_map = get_map
        self.post_map = post_map or {}
        self.posts = []

    def get(self, url):
        path = "/" + url.split("/", 3)[-1].split("?", 1)[0] if "/" in url[8:] else "/"
        path = path.rstrip("/") or "/"
        return self.get_map[path]

    def post(self, url, json=None):
        path = "/" + url.split("/", 3)[-1].split("?", 1)[0]
        self.posts.append((f"POST {path}", json))
        return self.post_map[path]


def _ok_client():
    page = '<a class="logo">baǵam</a><form data-check><input id="lotUrl"></form>/api/predict'
    return FakeClient(
        {
            "/": FakeResponse(text=page),
            "/stats": FakeResponse(text='<a class="logo">baǵam</a><div data-meta="districts"></div>/api/stats'),
            "/about": FakeResponse(text='<a class="logo">baǵam</a>О проекте Как считает модель'),
            "/api/health": FakeResponse(
                json_data={
                    "status": "ok",
                    "model_error_pct": 9.5,
                    "data_age_hours": 2.0,
                    "freshness": "ok",
                }
            ),
            "/api/demo": FakeResponse(json_data={"url": "https://krisha.kz/a/show/123"}),
            "/api/stats": FakeResponse(
                json_data={"total_listings": 10, "by_district": [{"district": "Бостандыкский"}]}
            ),
            "/api/heatmap": FakeResponse(json_data=[{"lat": 43.2, "lon": 76.9, "ppsm": 800000}]),
            "/api/forecast": FakeResponse(json_data={"city": {"current_ppsm": 750000}}),
        },
        {
            "/api/predict": FakeResponse(
                json_data={"listing_id": 123, "fair_price": 45_000_000, "verdict": "FAIR"}
            )
        },
    )


def test_run_smoke_checks_pages_api_and_demo_predict():
    client = _ok_client()

    checks = smoke_prod.run_smoke("https://prod.example", client=client)

    assert "page /" in checks
    assert "GET /api/health" in checks
    assert ("POST /api/predict", {"url": "https://krisha.kz/a/show/123"}) in client.posts
    assert "POST /api/predict demo" in checks
    assert "GET /api/heatmap" in checks


def test_run_smoke_fails_on_missing_page_marker():
    client = _ok_client()
    client.get_map["/"] = FakeResponse(text="<html>no evaluation form</html>")

    with pytest.raises(smoke_prod.SmokeError, match="page /"):
        smoke_prod.run_smoke("https://prod.example", client=client)


def test_run_smoke_fails_on_empty_api_data():
    client = _ok_client()
    client.get_map["/api/heatmap"] = FakeResponse(json_data=[])

    with pytest.raises(smoke_prod.SmokeError, match="/api/heatmap"):
        smoke_prod.run_smoke("https://prod.example", client=client)


def test_run_smoke_fails_on_stale_health():
    client = _ok_client()
    client.get_map["/api/health"] = FakeResponse(
        json_data={
            "status": "ok",
            "model_error_pct": 9.5,
            "data_age_hours": 31.2,
            "freshness": "stale",
        }
    )

    with pytest.raises(smoke_prod.SmokeError, match="freshness"):
        smoke_prod.run_smoke("https://prod.example", client=client)
