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


def test_share_button_is_gone():
    """Заглушка «Поделиться — скоро» убрана: ни разметки, ни стилей, ни обработчика."""
    html = _static("index.html")
    css = _static("design.css")

    assert "rshare" not in html and 'class="soon"' not in html
    assert ".soon" not in css
    assert "Уже делаем" not in html and "track('share'" not in html


def test_check_gives_visible_feedback():
    """Проверка ссылки: спиннер в кнопке, плашка успеха, адрес #check=<id> без повторных запросов."""
    html = _static("index.html")
    css = _static("design.css")

    assert "Проверяем…" in html and "Уже считаем, секунду…" in html
    assert 'id="rDone"' in html and "Готово — оценка по объявлению" in html
    assert "history.replaceState" in html and "hashchange" not in html
    assert 'id="repDemo"' in html and 'id="rHead"' in html
    assert "[aria-invalid=true]" in css and ".spin{" in css


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
