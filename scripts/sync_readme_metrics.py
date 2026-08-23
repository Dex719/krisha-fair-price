"""Обновляет блок актуальных метрик в README из models/model_meta.json.

Зачем. В README числа стояли руками и разъезжались с реальностью на каждом
ретрейне: бейдж MAPE 7.6% и таблица от 16.08 против меты 7.29% от 23.08.
Документация, которая врёт про качество модели, хуже отсутствующей — по ней
принимают решения.

Что делает. Между маркерами METRICS:BEGIN/METRICS:END кладёт метрики текущей
модели из меты. Всё остальное в README не трогает: таблица «как менялась
модель» — это история вех на фиксированном тесте, она замораживается намеренно.

Использование:
    python scripts/sync_readme_metrics.py           # переписать блок
    python scripts/sync_readme_metrics.py --check   # только проверить (exit 1)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
META = ROOT / "models" / "model_meta.json"
BEGIN = "<!-- METRICS:BEGIN -->"
END = "<!-- METRICS:END -->"


def _thousands(value) -> str:
    return f"{int(value):,}".replace(",", " ") if value is not None else "?"


def render(meta: dict) -> str:
    metrics = meta.get("metrics", {})
    model = metrics.get("model", {})
    ci = metrics.get("model_mape_ci") or {}
    trained_at = str(metrics.get("trained_at") or meta.get("trained_at") or "")[:10]
    mape = model.get("mape")
    mdape = model.get("mdape")
    mae = model.get("mae")
    r2 = model.get("r2")

    ci_text = ""
    if ci.get("lo") is not None and ci.get("hi") is not None:
        ci_text = f" (95% ДИ {ci['lo'] * 100:.2f}–{ci['hi'] * 100:.2f}%)"

    rows = [
        f"| MAPE | **{mape * 100:.2f}%**{ci_text} |" if mape is not None else None,
        f"| MdAPE | {mdape * 100:.2f}% |" if mdape is not None else None,
        f"| MAE | {mae / 1e6:.2f} млн ₸ |" if mae is not None else None,
        f"| R² | {r2:.4f} |" if r2 is not None else None,
        "| Обучено на | {} строк, тест {} |".format(
            _thousands(metrics.get("n_train")), _thousands(metrics.get("n_test"))
        ),
    ]

    valid = metrics.get("temporal_validity")
    if valid is False:
        note = (
            "Временная валидность **не подтверждена**: пока состав данных меняется "
            "вместе с временем, число описывает попадание по текущему стоку, а не "
            "экстраполяцию вперёд (`time_confounding` в мете)."
        )
    elif valid is True:
        note = "Временная валидность подтверждена (`temporal_validity: true` в мете)."
    else:
        note = "Вердикт о временной валидности в мете отсутствует."

    lines = [
        BEGIN,
        f"**Текущая прод-модель — ретрейн {trained_at}.** Числа берутся из "
        "`models/model_meta.json`; на сайте те же метрики живые "
        "(`/api/health`), в README их обновляет `scripts/sync_readme_metrics.py`.",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        *[r for r in rows if r],
        "",
        note,
        END,
    ]
    return "\n".join(lines)


def apply(text: str, block: str) -> str:
    if BEGIN not in text or END not in text:
        raise SystemExit(f"В README нет маркеров {BEGIN} / {END}")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return head + block + tail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="только проверить актуальность")
    args = parser.parse_args(argv)

    meta = json.loads(META.read_text(encoding="utf-8"))
    text = README.read_text(encoding="utf-8")
    updated = apply(text, render(meta))

    if args.check:
        if updated != text:
            print("README: блок метрик устарел — запусти scripts/sync_readme_metrics.py",
                  file=sys.stderr)
            return 1
        print("README: метрики совпадают с model_meta.json")
        return 0

    if updated == text:
        print("README: метрики уже актуальны")
        return 0
    README.write_text(updated, encoding="utf-8")
    print("README: блок метрик обновлён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
