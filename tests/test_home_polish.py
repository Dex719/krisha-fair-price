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


def test_share_report_shares_text_not_page_link():
    """Делимся содержанием отчёта: ссылка на наш сайт получателю ничего не покажет."""
    html = _static("index.html")

    assert "reportShareText" in html
    assert "Цена в объявлении: " in html
    assert "Справедливая оценка: " in html
    assert "Диапазон модели: " in html
    assert "Объявление: " in html
    assert "baǵam — справедливая цена квартир в Алматы" in html
    assert "navigator.share({title: 'Отчёт baǵam', text})" in html
    assert "writeText(location.href)" not in html


def test_scale_labels_never_leave_the_track():
    """У краёв шкалы подпись разворачивается внутрь, иначе её срезает."""
    html = _static("index.html")
    css = _static("design.css")

    assert "edgeL" in html and "edgeR" in html
    assert ".rmk.edgeL" in css and ".rmk.edgeR" in css
