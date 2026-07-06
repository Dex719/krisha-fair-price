"""Acceptance checks for issue #79 homepage/design-system transfer."""

from pathlib import Path

from krisha.api.app import CSP


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


def test_csp_keeps_fonts_self_hosted_only():
    assert "default-src 'self'" in CSP
    assert "font-src 'self'" in CSP
    assert "fonts.googleapis.com" not in CSP
    assert "fonts.gstatic.com" not in CSP
