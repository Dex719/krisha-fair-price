"""Acceptance checks for issue #85 homepage polish."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_feature_names_cover_known_model_keys_and_hide_unknowns():
    html = _static("index.html")

    for key in (
        "completion_year",
        "apartments_count",
        "dist_big_road_km",
        "dist_industrial_km",
        "hex7_ppsm",
        "hex8_ppsm",
        "knn_n",
        "district_mismatch",
        "is_new_building",
        "security_count",
        "has_security_guard",
        "has_intercom",
        "has_video_surveillance",
        "category",
        "lat",
        "lon",
        "dist_center_km",
        "district_ppsm",
        "microdistrict_ppsm",
        "floor_ratio",
    ):
        assert f"{key}:" in html
    assert "console.warn('Неизвестный фактор скрыт:',key)" in html
    assert "FEATURE_NAMES[f.feature]||f.feature" not in html


def test_factor_hints_are_tooltips_not_inline_subtext():
    html = _static("index.html")
    css = _static("design.css")

    assert "role=\"tooltip\"" in html
    assert "aria-describedby" in html
    assert "has-hint" in html
    assert "bindFactorHints()" in html
    assert "tip-open" in html
    assert "f.hint?' <span class=\"sub\">· '+esc(f.hint)" not in html
    assert ".ftip" in css
    assert ".fr.has-hint .fname" in css
    assert ".fr.has-hint:focus" in css


def test_home_market_chart_uses_ppsm_hist_and_no_median_tooltip():
    html = _static("index.html")
    css = _static("design.css")

    assert "stats.ppsm_hist" in html
    assert "stats.price_hist" not in html
    assert "from_ppsm" in html and "to_ppsm" in html
    assert "fmtPpsm(stats.median_ppsm)" in html
    assert "histBinIndex(hist,ppsm)" in html
    assert "tipBand" not in html
    assert "band-hot" not in html
    assert "band-hot" not in css
    assert "cband" not in css
    assert "Распределение цен за м² по рынку Алматы" in html


def test_share_report_uses_text_not_location_href():
    html = _static("index.html")

    assert "reportShareText" in html
    assert "Цена в объявлении: " in html
    assert "Справедливая оценка: " in html
    assert "Диапазон модели: " in html
    assert "Объявление: " in html
    assert "baǵam — справедливая цена квартир в Алматы" in html
    assert "navigator.share({title:'Отчёт baǵam',text})" in html
    assert "writeText(text)" in html
    assert "url:location.href" not in html
    assert "writeText(location.href)" not in html
