#!/usr/bin/env python
"""Метрический гейт для еженедельного переобучения.

Если модель стала хуже сверх допуска — выходим с кодом 1, и workflow
НЕ коммитит новую модель (в проде остаётся старая).

Честный режим: если train() запускался с --compare-old, в новом meta есть
metrics["old_model"] — старая модель, оценённая на том же свежем test-сплите.
Сравнение на одной выборке не путает дрейф данных с деградацией модели.
Fallback (нет old_model): сравниваем с метриками из прошлого meta —
test-выборки разных недель не совпадают, поэтому допуски шире по смыслу.

Запуск: python scripts/model_gate.py old_meta.json new_meta.json
"""

import argparse
import json
import sys
from pathlib import Path

# Допуск на шум: MAPE может «дышать» между запусками из-за новых данных.
MAPE_TOLERANCE = 0.005  # +0.5 п.п. абсолютно
MAE_TOLERANCE_REL = 0.05  # +5% относительно


def load_metrics(path: str) -> dict:
    meta = json.loads(Path(path).read_text())
    return meta["metrics"]["model"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Гейт: новая модель не хуже старой")
    parser.add_argument("old_meta")
    parser.add_argument("new_meta")
    parser.add_argument("--summary", help="Файл для markdown-отчёта (GITHUB_STEP_SUMMARY)")
    args = parser.parse_args()

    new_meta = json.loads(Path(args.new_meta).read_text())
    new = new_meta["metrics"]["model"]
    old_on_new_test = new_meta["metrics"].get("old_model")
    if old_on_new_test:
        old = old_on_new_test
        mode = "старая модель на том же test-сплите (честное сравнение)"
    else:
        old = load_metrics(args.old_meta)
        mode = "метрики прошлой недели (разные test-выборки — возможен дрейф данных)"

    mape_ok = new["mape"] <= old["mape"] + MAPE_TOLERANCE
    mae_ok = new["mae"] <= old["mae"] * (1 + MAE_TOLERANCE_REL)
    passed = mape_ok and mae_ok

    lines = [
        "### Метрический гейт",
        "",
        f"_База сравнения: {mode}_",
        "",
        "| Метрика | Было | Стало | OK |",
        "|---|---|---|---|",
        f"| MAPE | {old['mape']:.2%} | {new['mape']:.2%} | {'✅' if mape_ok else '❌'} |",
        f"| MAE | {old['mae'] / 1e6:.2f}M ₸ | {new['mae'] / 1e6:.2f}M ₸ | {'✅' if mae_ok else '❌'} |",
        f"| R² | {old['r2']:.3f} | {new['r2']:.3f} | — |",
        "",
        "**Вердикт:** " + ("✅ деплоим" if passed else "❌ новая модель хуже — оставляем старую"),
    ]
    report = "\n".join(lines)
    print(report)
    if args.summary:
        with open(args.summary, "a") as fh:
            fh.write(report + "\n")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
