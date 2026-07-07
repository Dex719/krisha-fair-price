"""Production smoke-check for the live Hugging Face Space.

Run locally:
    python scripts/smoke_prod.py
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://dex719-krisha-fair-price.hf.space"
DEFAULT_TIMEOUT_S = 30.0

PAGE_MARKERS = {
    "/": ('class="logo"', "baǵam", 'id="form"', 'id="url"', "/api/predict"),
    "/stats": ('class="logo"', "baǵam", "Медиана по районам", "/api/stats"),
    "/about": ('class="logo"', "baǵam", "О проекте", "Как считает модель"),
}


class SmokeError(AssertionError):
    """Raised when the production smoke-check sees an unhealthy response."""


def run_smoke(
    base_url: str = DEFAULT_BASE_URL,
    *,
    client: httpx.Client | Any | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> list[str]:
    """Run all production smoke checks and return labels for passed checks."""

    normalized_base_url = base_url.rstrip("/")
    if client is not None:
        return _run_with_client(normalized_base_url, client)

    with httpx.Client(timeout=timeout, follow_redirects=True) as http_client:
        return _run_with_client(normalized_base_url, http_client)


def _run_with_client(base_url: str, client: httpx.Client | Any) -> list[str]:
    checks: list[str] = []

    for path, markers in PAGE_MARKERS.items():
        label = f"page {path}"
        response = _get(client, base_url, path, label)
        _expect_status(label, response)
        _expect_markers(label, response.text, markers)
        checks.append(label)

    health = _get_json(client, base_url, "/api/health", "GET /api/health")
    if not isinstance(health, dict) or "model_error_pct" not in health:
        raise SmokeError("GET /api/health: response must include model_error_pct")
    if health.get("freshness") != "ok":
        raise SmokeError(
            "GET /api/health: freshness must be ok "
            f"(freshness={health.get('freshness')!r}, data_age_hours={health.get('data_age_hours')!r})"
        )
    if not isinstance(health.get("data_age_hours"), int | float):
        raise SmokeError("GET /api/health: data_age_hours must be a number when freshness is ok")
    checks.append("GET /api/health")

    demo = _get_json(client, base_url, "/api/demo", "GET /api/demo")
    if not isinstance(demo, dict):
        raise SmokeError("GET /api/demo: response must be a JSON object")
    demo_url = demo.get("url")
    if not isinstance(demo_url, str) or "krisha.kz/a/show/" not in demo_url:
        raise SmokeError("GET /api/demo: response must include a live krisha.kz/a/show URL")
    checks.append("GET /api/demo")

    predict = _post_json(
        client,
        base_url,
        "/api/predict",
        "POST /api/predict demo",
        payload={"url": demo_url},
    )
    if not isinstance(predict, dict):
        raise SmokeError("POST /api/predict demo: response must be a JSON object")
    fair_price = predict.get("fair_price")
    if not isinstance(fair_price, int | float) or fair_price <= 0:
        raise SmokeError("POST /api/predict demo: fair_price must be a positive number")
    if not (predict.get("verdict") or predict.get("listing_id") or predict.get("title")):
        raise SmokeError("POST /api/predict demo: response does not look meaningful")
    checks.append("POST /api/predict demo")

    stats = _get_json(client, base_url, "/api/stats", "GET /api/stats")
    _expect_non_empty_stats(stats)
    checks.append("GET /api/stats")

    heatmap = _get_json(client, base_url, "/api/heatmap", "GET /api/heatmap")
    if not isinstance(heatmap, list) or not heatmap:
        raise SmokeError("GET /api/heatmap: expected non-empty list of points")
    checks.append("GET /api/heatmap")

    forecast = _get_json(client, base_url, "/api/forecast", "GET /api/forecast")
    _expect_non_empty_forecast(forecast)
    checks.append("GET /api/forecast")

    return checks


def _url(base_url: str, path: str) -> str:
    return f"{base_url}{path}"


def _get(client: httpx.Client | Any, base_url: str, path: str, label: str) -> Any:
    try:
        return client.get(_url(base_url, path))
    except httpx.HTTPError as exc:
        raise SmokeError(f"{label}: request failed: {exc}") from exc


def _post_json(
    client: httpx.Client | Any,
    base_url: str,
    path: str,
    label: str,
    *,
    payload: dict[str, Any],
) -> Any:
    try:
        response = client.post(_url(base_url, path), json=payload)
    except httpx.HTTPError as exc:
        raise SmokeError(f"{label}: request failed: {exc}") from exc
    _expect_status(label, response)
    return _parse_json(label, response)


def _get_json(client: httpx.Client | Any, base_url: str, path: str, label: str) -> Any:
    response = _get(client, base_url, path, label)
    _expect_status(label, response)
    return _parse_json(label, response)


def _expect_status(label: str, response: Any) -> None:
    if response.status_code == 200:
        return
    snippet = str(getattr(response, "text", "")).replace("\n", " ")[:180]
    raise SmokeError(f"{label}: expected HTTP 200, got {response.status_code}. {snippet}")


def _expect_markers(label: str, text: str, markers: Iterable[str]) -> None:
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SmokeError(f"{label}: missing markup markers: {', '.join(missing)}")


def _parse_json(label: str, response: Any) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise SmokeError(f"{label}: response is not valid JSON") from exc


def _expect_non_empty_stats(stats: Any) -> None:
    if not isinstance(stats, dict):
        raise SmokeError("GET /api/stats: response must be a JSON object")
    total_listings = stats.get("total_listings")
    has_total = isinstance(total_listings, int | float) and total_listings > 0
    has_districts = isinstance(stats.get("by_district"), list) and bool(stats["by_district"])
    has_hist = isinstance(stats.get("ppsm_hist"), list) and bool(stats["ppsm_hist"])
    if not has_total or not (has_districts or has_hist):
        raise SmokeError("GET /api/stats: expected total_listings and non-empty market data")


def _expect_non_empty_forecast(forecast: Any) -> None:
    if not isinstance(forecast, dict):
        raise SmokeError("GET /api/forecast: response must be a JSON object")
    has_city = isinstance(forecast.get("city"), dict) and bool(forecast["city"])
    has_districts = isinstance(forecast.get("districts"), list) and bool(forecast["districts"])
    if not has_city and not has_districts:
        raise SmokeError("GET /api/forecast: expected non-empty city or district forecast")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run production smoke checks for baǵam.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PROD_BASE_URL", DEFAULT_BASE_URL),
        help=f"Production base URL (default: {DEFAULT_BASE_URL}; env: PROD_BASE_URL)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("SMOKE_TIMEOUT_S", DEFAULT_TIMEOUT_S)),
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_S}; env: SMOKE_TIMEOUT_S)",
    )
    args = parser.parse_args(argv)

    try:
        checks = run_smoke(args.base_url, timeout=args.timeout)
    except SmokeError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"SMOKE OK: {args.base_url.rstrip('/')}")
    for check in checks:
        print(f"  ✓ {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
