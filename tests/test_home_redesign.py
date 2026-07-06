"""Acceptance checks for issue #79 homepage/design-system transfer."""

from pathlib import Path

from fastapi.testclient import TestClient

from krisha import db
from krisha.api import app as app_module
from krisha.api.app import CSP, app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_home_uses_bagam_meta_design_css_and_local_favicon():
    html = _static("index.html")

    assert (
        "<title>Справедливая цена квартиры в Алматы по ссылке с Krisha │ baǵam</title>"
        in html
    )
    assert '<meta name="description"' in html
    assert 'href="/static/design.css"' in html
    assert 'rel="icon" type="image/svg+xml" href="/static/favicon.svg"' in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_design_css_contains_master_tokens_and_components():
    css = _static("design.css")

    for token in (
        "--band:#16382B",
        "--green:#2E6E52",
        "--paper:#F4F3EF",
        "--surface:#FBFAF7",
        "--ink:#191B1A",
        "--rule:#E4E4E0",
    ):
        assert token in css
    assert "body.dark" in css
    for component in (
        ".btn-primary",
        ".btn-sec",
        ".klink",
        ".chip",
        ".pill",
        ".lead-dots",
        ".scale",
        ".zone",
        ".pin",
        ".receipt-skeleton",
        ".r-empty",
        ".skchart",
        ".loadnote",
        ".sk",
    ):
        assert component in css


def test_design_css_self_hosts_golos_text_weights():
    css = _static("design.css")

    assert '@font-face' in css
    assert 'font-family:"Golos Text"' in css or 'font-family: "Golos Text"' in css
    assert "font-display:swap" in css or "font-display: swap" in css
    assert "/static/fonts/" in css
    for weight in ("400", "500", "600", "700"):
        assert f"font-weight:{weight}" in css or f"font-weight: {weight}" in css


def test_home_keeps_api_flow_flags_theme_and_delayed_skeleton():
    html = _static("index.html")

    assert 'id="form"' in html
    assert 'id="url"' in html and 'type="url"' in html and 'inputmode="url"' in html
    assert "/api/predict" in html
    assert "/api/flags/" in html
    assert "flags_pending" in html
    assert "text_flags" in html
    assert "localStorage.setItem" in html
    assert "receipt-skeleton" in html
    assert "setTimeout" in html and "300" in html
    assert "spinner" not in html


def test_home_uses_live_stats_demo_endpoint_and_empty_first_visit():
    html = _static("index.html")

    assert "/api/stats" in html
    assert "/api/demo" in html
    assert "telegram-web-app.js" in html
    assert "tg.ready" in html and "tg.expand" in html
    assert "r-empty" in html
    assert "Здесь появится отчёт об оценке" in html
    assert "оценка ещё не выполнялась" in html
    assert "re-ghost" in html
    assert "Показать на примере" in html
    assert "hasRealReceipt" in html
    assert "scam-note" in html
    assert "Пример отчёта" not in html
    for fake in (
        "11 357",
        "1 214",
        "716 тыс",
        "760000000",
        "5 июля 2026",
        "данные обновлены сегодня утром",
        "33 700 000",
        "33,7 млн",
        "38,5 млн",
        "36,2",
        "41,1",
        "Мамыр-4",
        "Мамыр-1",
        "Аксай-3",
        "692 тыс",
        "705 тыс",
        "689 тыс",
    ):
        assert fake not in html


def test_home_has_stats_loading_skeleton_and_button_unlock_paths():
    html = _static("index.html")

    assert "skchart" in html
    assert "market-skeleton" in html
    assert 'aria-busy="true"' in html
    assert "Загружаем свежие данные рынка — обычно это пара секунд" in html
    assert 'disabled aria-disabled="true"' in html
    assert "setMarketLoading(true)" in html
    assert "setMarketLoading(false)" in html
    assert "Статистика рынка временно недоступна — оценку можно запустить" in html
    assert "showMarketStatsError" in html
    assert "renderEmptyReceipt" in html
    assert "initChartTips()" in html
    assert 'id="lot-legend"' in html
    assert "document.getElementById('lot-legend')?.remove()" in html


def test_home_chart_uses_whole_price_units_for_price_hist():
    html = _static("index.html")

    assert "Рынок сегодня · цены квартир, Алматы" in html
    assert "Рынок сегодня · цена за м², Алматы" not in html
    assert "box.dataset.medianLabel='медиана рынка — '+fmtMln(stats.median_price)" in html
    assert "fmtPpsm(stats.median_ppsm):fmtMln(stats.median_price)" not in html


def test_csp_keeps_fonts_self_hosted_only():
    assert "default-src 'self'" in CSP
    assert "font-src 'self'" in CSP
    assert "fonts.googleapis.com" not in CSP
    assert "fonts.gstatic.com" not in CSP


def test_demo_endpoint_returns_active_listing_url_and_is_rate_limited(tmp_path, monkeypatch):
    db_path = tmp_path / "krisha.db"
    db.init_db(db_path)
    db.upsert_listing(
        {
            "id": 987654321,
            "url": "https://krisha.kz/a/show/987654321",
            "title": "Демо",
            "price": 42_000_000,
            "area": 55.0,
            "rooms": 2,
            "district": "Auezovskiy_r-n",
            "source": "test",
        },
        db_path=db_path,
    )
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    app_module._rate.clear()

    client = TestClient(app)
    resp = client.get("/api/demo", headers={"x-forwarded-for": "203.0.113.79"})
    assert resp.status_code == 200
    assert resp.json() == {
        "listing_id": 987654321,
        "url": "https://krisha.kz/a/show/987654321",
    }

    app_module._rate.clear()
    for _ in range(app_module.RATE_LIMIT):
        assert (
            client.get("/api/demo", headers={"x-forwarded-for": "203.0.113.80"}).status_code
            == 200
        )
    limited = client.get("/api/demo", headers={"x-forwarded-for": "203.0.113.80"})
    assert limited.status_code == 429
