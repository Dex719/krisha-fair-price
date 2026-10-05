"""Мелкие контракты главной, которые легко потерять при правке вёрстки."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_factor_rows_keep_direction_value_and_bar():
    """Каждый фактор объясняет себя сам: направление, сумма, имя и шкала вклада."""
    html = _static("index.html")
    css = _static("design.css")

    assert "class=\"fx " in html
    for part in ('class="fxd"', 'class="fxv"', 'class="fxn"', 'class="fxb"'):
        assert part in html, f"пропала часть карточки фактора: {part}"
    assert ".fx.pos" in css and ".fx.neg" in css
    assert ".fxh" in css, "подсказка модели по фактору"
    assert "f.hint" in html, "подсказка приходит из API"


def test_share_button_is_clickable_and_says_soon():
    """«Поделиться» ещё в работе: кнопка видна и нажимается, помечена «скоро» и на
    нажатие отвечает «Уже делаем»; событие share считает, сколько её ждут."""
    html = _static("index.html")
    css = _static("design.css")

    assert '<span>Поделиться отчётом</span><em class="soon">скоро</em></button>' in html
    assert ".rbtn .soon{" in css
    assert "lbl.textContent = 'Уже делаем'" in html
    assert "track('share'" in html
    # неработающая отправка не осталась мёртвым кодом
    assert "navigator.share" not in html and "reportShareText" not in html


def test_scale_labels_never_leave_the_track():
    """У краёв шкалы подпись разворачивается внутрь, иначе её срезает."""
    html = _static("index.html")
    css = _static("design.css")

    assert "edgeL" in html and "edgeR" in html
    assert ".rmk.edgeL" in css and ".rmk.edgeR" in css


def test_report_has_rental_yield_block():
    """Продажа: «если сдавать» — аренда, валовая доходность и окупаемость из rental_yield."""
    html = _static("index.html")
    css = _static("design.css")

    assert 'id="rYield"' in html and "function renderYield" in html
    for field in ("monthly_rent", "gross_yield_pct", "payback_years", "district_yield_pct", "assumes_renovation"):
        assert field in html, f"не используется поле {field}"
    assert "Валовая доходность" in html, "честная оговорка: без налогов и простоя"
    assert ".ryield" in css or ".rygrid" in css
