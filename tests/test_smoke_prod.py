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


# --- 503 от источника: смоук ретраит, но не зеленеет молча ------------------
# `.kiro/specs/safeline-468`: krisha закрыта WAF SafeLine и вероятностно
# отдаёт anti-bot челлендж, прод в ответ честно говорит 503. Одна попытка
# проверяла бы везение, а не сервис; бесконечная терпимость прятала бы поломку.


class SequenceResponse:
    """Очередь ответов на один и тот же запрос (по вызову за штуку)."""

    def __init__(self, responses):
        self._responses = list(responses)

    def pop(self):
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


class SequencedClient(FakeClient):
    def post(self, url, json=None):
        path = "/" + url.split("/", 3)[-1].split("?", 1)[0]
        self.posts.append((f"POST {path}", json))
        entry = self.post_map[path]
        return entry.pop() if isinstance(entry, SequenceResponse) else entry


def _busy(detail="Источник временно не отдаёт объявление, попробуй ещё раз"):
    return FakeResponse(status_code=503, text='{"detail": "%s"}' % detail)


def _sequenced_client(predict_responses):
    ok = _ok_client()
    client = SequencedClient(ok.get_map, {"/api/predict": SequenceResponse(predict_responses)})
    return client


def test_predict_retries_past_transient_503(monkeypatch):
    """AC-4.1: два челленджа подряд, третья попытка успешна — смоук зелёный."""
    monkeypatch.setattr(smoke_prod.time, "sleep", lambda _s: None)
    good = FakeResponse(
        json_data={"listing_id": 123, "fair_price": 45_000_000, "verdict": "FAIR"}
    )
    client = _sequenced_client([_busy(), _busy(), good])

    checks = smoke_prod.run_smoke("https://prod.example", client=client)

    assert "POST /api/predict demo" in checks
    assert len(client.posts) == 3


def test_predict_fails_after_all_attempts_are_503(monkeypatch):
    """Источник не пустил ни разу — падаем, назвав внешнюю причину."""
    monkeypatch.setattr(smoke_prod.time, "sleep", lambda _s: None)
    client = _sequenced_client([_busy()])

    with pytest.raises(smoke_prod.SmokeError, match="SafeLine"):
        smoke_prod.run_smoke("https://prod.example", client=client)
    assert len(client.posts) == smoke_prod.PREDICT_ATTEMPTS


def test_predict_does_not_retry_real_server_errors(monkeypatch):
    """502 — поломка сервиса, а не источника: падаем сразу, без повторов."""
    monkeypatch.setattr(smoke_prod.time, "sleep", lambda _s: None)
    client = _sequenced_client([FakeResponse(status_code=502, text="boom")])

    with pytest.raises(smoke_prod.SmokeError, match="expected HTTP 200, got 502"):
        smoke_prod.run_smoke("https://prod.example", client=client)
    assert len(client.posts) == 1


def test_failed_predict_reports_scrape_counters_from_metrics(monkeypatch):
    """AC-11.1 (safeline-468 §7): логи контейнера HF живут меньше суток, так что
    в тексте падения должно быть видно, что krisha отдавала IP прода."""
    monkeypatch.setattr(smoke_prod.time, "sleep", lambda _s: None)
    client = _sequenced_client([FakeResponse(status_code=502, text="boom")])
    client.get_map["/api/metrics"] = FakeResponse(json_data={"counters": {
        "scrape_http_403": 3, "scrape_waf_block": 3, "rate_limited": 1,
    }})

    with pytest.raises(smoke_prod.SmokeError) as err:
        smoke_prod.run_smoke("https://prod.example", client=client)

    text = str(err.value)
    assert "got 502" in text and "'scrape_http_403': 3" in text and "scrape_waf_block" in text
    assert "rate_limited" not in text, "в диагностику — только исходы скрейпа и предикта"


def test_unavailable_metrics_do_not_mask_the_original_error(monkeypatch):
    monkeypatch.setattr(smoke_prod.time, "sleep", lambda _s: None)
    client = _sequenced_client([FakeResponse(status_code=502, text="boom")])  # /api/metrics нет

    with pytest.raises(smoke_prod.SmokeError, match=r"got 502\. boom$"):
        smoke_prod.run_smoke("https://prod.example", client=client)


def test_removed_demo_listing_is_replaced_not_reported_as_breakage(monkeypatch):
    """predict-edge-listings AC-5.1: демо-лот сняли — берём другой, смоук зелёный."""
    monkeypatch.setattr(smoke_prod.time, "sleep", lambda _s: None)
    gone = FakeResponse(status_code=404, text='{"detail": "Объявление не найдено"}')
    good = FakeResponse(json_data={"listing_id": 7, "fair_price": 45_000_000, "verdict": "FAIR"})
    client = _sequenced_client([gone, good])

    checks = smoke_prod.run_smoke("https://prod.example", client=client)

    assert "POST /api/predict demo" in checks
    assert len(client.posts) == 2


def test_only_removed_demo_listings_fail_with_explanation(monkeypatch):
    monkeypatch.setattr(smoke_prod.time, "sleep", lambda _s: None)
    gone = FakeResponse(status_code=404, text='{"detail": "Объявление не найдено"}')
    client = _sequenced_client([gone])

    with pytest.raises(smoke_prod.SmokeError, match="демо-лота подряд"):
        smoke_prod.run_smoke("https://prod.example", client=client)
    assert len(client.posts) == smoke_prod.DEMO_REPICKS + 1


def test_retry_after_header_is_honoured_and_capped():
    assert smoke_prod._retry_after_s(FakeResponse(), 4.0) == 4.0

    class WithHeader(FakeResponse):
        headers = {"Retry-After": "7"}

    class Absurd(FakeResponse):
        headers = {"Retry-After": "9000"}

    assert smoke_prod._retry_after_s(WithHeader(), 4.0) == 7.0
    assert smoke_prod._retry_after_s(Absurd(), 4.0) == smoke_prod.PREDICT_RETRY_WAIT_MAX_S
