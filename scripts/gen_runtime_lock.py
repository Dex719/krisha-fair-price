"""Генерирует requirements-runtime.lock из requirements.lock (issue #119).

Прод-образ ставил весь requirements.lock, а он тащит matplotlib==3.11.0,
pillow, plotly, narwhals, graphviz, contourpy, fonttools, kiwisolver —
plotting-хвост, который catboost объявляет ОБЯЗАТЕЛЬНЫМ в своих Requires-Dist
(значит `uv pip compile` их резолвит независимо от extras в pyproject.toml),
хотя реально нужен только для `plot_*`/`calc_feature_statistics`.
`CatBoostRegressor.fit/predict/save_model/load_model/get_feature_importance`
работают без них (проверено вручную в чистом venv с `catboost --no-deps` +
numpy/pandas/scipy/six).

requirements.lock остаётся полным резолвом uv (используется для
`pip install -e ".[dev]"` локально и как источник правды по версиям).
requirements-runtime.lock — тот же резолв минус исключённый хвост; ставится
в Dockerfile через `pip install --no-deps` — лок уже полностью плоский
(uv развернул весь транзитивный граф), --no-deps просто не даёт pip заново
подтянуть matplotlib и т.п. из Requires-Dist самого catboost.

Регенерировать вместе с requirements.lock: `make lock`.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_IN = ROOT / "requirements.lock"
LOCK_OUT = ROOT / "requirements-runtime.lock"

# Пакеты, нужные catboost только для графиков/деревьев (plot_*,
# calc_feature_statistics), не для fit()/predict()/save_model()/load_model().
PLOTTING_TAIL = {
    "matplotlib", "contourpy", "cycler", "fonttools", "kiwisolver", "pillow",
    "pyparsing", "graphviz", "plotly", "narwhals", "packaging",
}

HEADER = (
    "# АВТОГЕНЕРИРУЕТСЯ: scripts/gen_runtime_lock.py из requirements.lock (make lock).\n"
    "# issue #119 — тот же резолв минус plotting-хвост catboost (matplotlib/\n"
    "# plotly/graphviz/pillow/...), не нужный serving-пути (/api/predict).\n"
    "# Ставить строго с `pip install --no-deps`: лок уже полностью плоский,\n"
    "# без --no-deps pip заново подтянет matplotlib из Requires-Dist catboost.\n"
)


def parse_blocks(text: str) -> list[tuple[str, str]]:
    """[(package_name, full_block_text)]. Блок = строка `pkg==ver` плюс все
    последующие строки-комментарии ("# via ...") до следующего `pkg==ver`."""
    blocks: list[tuple[str, str]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        is_pkg_line = "==" in line and not line.startswith((" ", "#"))
        if is_pkg_line:
            if current_name is not None:
                blocks.append((current_name, "".join(current_lines)))
            current_name = line.split("==", 1)[0].strip()
            current_lines = [line]
        elif current_name is not None:
            current_lines.append(line)
        # строки до первого pkg==ver (шапка автогенерации uv) отбрасываются
    if current_name is not None:
        blocks.append((current_name, "".join(current_lines)))
    return blocks


def filter_runtime_lock(text: str) -> str:
    kept = [block for name, block in parse_blocks(text) if name not in PLOTTING_TAIL]
    return HEADER + "\n" + "".join(kept)


def main() -> None:
    runtime_lock = filter_runtime_lock(LOCK_IN.read_text(encoding="utf-8"))
    LOCK_OUT.write_text(runtime_lock, encoding="utf-8")
    print(f"wrote {LOCK_OUT} ({len(runtime_lock.splitlines())} lines)")


if __name__ == "__main__":
    main()
