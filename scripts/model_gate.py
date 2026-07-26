#!/usr/bin/env python
"""Метрический гейт для еженедельного переобучения.

Если модель стала хуже сверх допуска — выходим с кодом 1, и workflow
НЕ коммитит новую модель (в проде остаётся старая).

Честный режим: если train() запускался с --compare-old, в новом meta есть
metrics["old_model"] — старая модель, оценённая на том же свежем test-сплите.
Сравнение на одной выборке не путает дрейф данных с деградацией модели.
Fallback (нет old_model И нет old_model_error — --compare-old не запускался
вовсе): сравниваем с метриками из прошлого meta — test-выборки разных недель
не совпадают, поэтому допуски шире по смыслу.

issue #106:
- fail-closed: если --compare-old запускался, но оценка старой модели
  свалилась с ошибкой (old_model_error в meta — обычно набор фичей
  разошёлся), НЕ уходим в fallback-сравнение вслепую — блокируем публикацию.
- гейтим покрытие доверительного интервала (coverage_test) и его ширину —
  раньше квантильные модели переобучались и коммитились всегда, даже если
  покрытие проваливалось до 0.6, единственный гейт этого не видел.
- если рядом лежит models/model_gate_samples.json (пары APE новая/старая
  модель по каждой test-строке — пишет train.py), точность деградации MAPE
  проверяем парным бутстрепом разницы средних APE, а не плоским допуском
  ±0.5 п.п.: на ~1000-2000 строках одного сплита это 1.5-2σ шума — плоский
  допуск и пропускает деградации, и блокирует реальные улучшения.

Запуск: python scripts/model_gate.py old_meta.json new_meta.json
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# Допуск на шум: MAE может «дышать» между запусками из-за новых данных.
MAPE_TOLERANCE = 0.005  # +0.5 п.п. абсолютно — fallback, когда бутстреп недоступен
MAE_TOLERANCE_REL = 0.05  # +5% относительно

# issue #106 (доп.): коэффициент допуска на покрытие интервала — 0.80 target
# минус 0.02, и допуск на рост ширины интервала при равном покрытии.
COVERAGE_TOLERANCE = 0.02
WIDTH_TOLERANCE_REL = 0.10

# Парный бутстреп разницы APE (новая - старая): если нижняя граница CI разницы
# средних > этот допуск — деградация статистически значима, не шум.
BOOTSTRAP_TOLERANCE = 0.005  # +0.5 п.п.
BOOTSTRAP_N = 2000
BOOTSTRAP_CI = 0.90  # двусторонний; проверяем нижнюю границу


def bootstrap_ape_diff(ape_new: list[float], ape_old: list[float], seed: int = 42) -> tuple[float, float, float]:
    """Парный бутстреп mean(ape_new) - mean(ape_old). Возвращает (lower, upper, point)."""
    diff = np.asarray(ape_new) - np.asarray(ape_old)
    rng = np.random.default_rng(seed)
    n = len(diff)
    idx = rng.integers(0, n, size=(BOOTSTRAP_N, n))
    boot_means = diff[idx].mean(axis=1)
    tail = (1 - BOOTSTRAP_CI) / 2
    lower = float(np.quantile(boot_means, tail))
    upper = float(np.quantile(boot_means, 1 - tail))
    return lower, upper, float(diff.mean())


def main() -> None:
    parser = argparse.ArgumentParser(description="Гейт: новая модель не хуже старой")
    parser.add_argument("old_meta")
    parser.add_argument("new_meta")
    parser.add_argument(
        "--samples", default="models/model_gate_samples.json",
        help="Файл с парными APE (новая/старая модель) для бутстрепа",
    )
    parser.add_argument("--summary", help="Файл для markdown-отчёта (GITHUB_STEP_SUMMARY)")
    args = parser.parse_args()

    new_meta = json.loads(Path(args.new_meta).read_text(encoding="utf-8"))
    new = new_meta["metrics"]["model"]
    new_interval = new_meta["metrics"].get("interval", {})
    old_on_new_test = new_meta["metrics"].get("old_model")
    old_model_error = new_meta["metrics"].get("old_model_error")

    lines = ["### Метрический гейт", ""]
    checks: list[tuple[str, bool]] = []

    if old_model_error:
        # issue #106: fail-closed — --compare-old запускался и явно упал,
        # сравнение с прошлым meta вслепую (разные test-выборки) маскировало бы это.
        lines += [
            f"**Вердикт:** ❌ старая модель не оценилась на новом test-сплите "
            f"(`{old_model_error}`) — блокируем публикацию (fail-closed).",
        ]
        print("\n".join(lines))
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        sys.exit(1)

    # issue #158: ранжировать две модели на непредставительном тесте нельзя.
    # Сейчас test — это 79% одного района (TVD 0.46 при пороге 0.20), потому
    # что сбор был ограничен лимитом 1000/день и шёл по районам по алфавиту.
    # Модель, которая лучше на Алмалинском и хуже по городу, такой гейт
    # ПРОЙДЁТ — и уедет в прод как улучшение.
    #
    # Поведение — предупреждение, а не блокировка: заблокировать публикацию
    # означало бы заморозить модель до тех пор, пока не выровняется сбор, то
    # есть оставить прод на заведомо более старой модели. Это лечится само
    # (issue #152), а до тех пор решение принимает человек, видя оговорку.
    validity = new_meta["metrics"].get("test_representativeness") or {}
    temporal_ok = new_meta["metrics"].get("temporal_validity")
    if validity and not validity.get("representative", True):
        tvd = validity.get("tvd", {})
        lines += [
            f"⚠️ **Тест непредставителен:** worst TVD {validity.get('worst_tvd')} "
            f"при пороге {validity.get('threshold')} (по колонкам: {tvd}). "
            "Сравнение моделей на такой выборке ранжирует их по перекошенному "
            "срезу, а не по городу — читайте вердикт с этой поправкой.",
            "",
        ]
    elif temporal_ok is False:
        lines += [
            "⚠️ **Временная валидность не подтверждена** — оценка описывает "
            "текущий сток, а не экстраполяцию вперёд.",
            "",
        ]

    old_meta_full = json.loads(Path(args.old_meta).read_text(encoding="utf-8"))
    old_interval = old_meta_full.get("metrics", {}).get("interval", {})
    if old_on_new_test:
        old = old_on_new_test
        mode = "старая модель на том же test-сплите (честное сравнение)"
    else:
        old = old_meta_full["metrics"]["model"]
        mode = "метрики прошлой недели (разные test-выборки — возможен дрейф данных)"

    # --- MAPE/MAE: бутстреп, если есть парные сэмплы, иначе плоский допуск ---
    samples_path = Path(args.samples)
    bootstrap_note = None
    if old_on_new_test and samples_path.exists():
        try:
            samples = json.loads(samples_path.read_text(encoding="utf-8"))
            lower, upper, point = bootstrap_ape_diff(samples["ape_new"], samples["ape_old"])
            mape_ok = lower <= BOOTSTRAP_TOLERANCE
            bootstrap_note = (
                f"Δ APE (новая-старая), бутстреп {int(BOOTSTRAP_CI*100)}% CI: "
                f"{point:+.2%} [{lower:+.2%}, {upper:+.2%}]"
            )
        except Exception as exc:  # файл битый/несовместимый — fallback на плоский допуск
            mape_ok = new["mape"] <= old["mape"] + MAPE_TOLERANCE
            bootstrap_note = f"бутстреп недоступен ({exc}), плоский допуск"
    else:
        mape_ok = new["mape"] <= old["mape"] + MAPE_TOLERANCE
    mae_ok = new["mae"] <= old["mae"] * (1 + MAE_TOLERANCE_REL)
    checks.append(("mape", mape_ok))
    checks.append(("mae", mae_ok))

    # --- issue #106: гейт покрытия и ширины доверительного интервала ---
    coverage_ok = width_ok = True
    coverage_note = width_note = None
    if new_interval:
        target = new_interval.get("target_coverage", 0.80)
        coverage = new_interval.get("coverage_test")
        if coverage is not None:
            coverage_ok = coverage >= target - COVERAGE_TOLERANCE
            coverage_note = f"{coverage:.2%} (допуск ≥ {target - COVERAGE_TOLERANCE:.2%})"
        old_width = old_interval.get("median_width_pct")
        new_width = new_interval.get("median_width_pct")
        if old_width and new_width is not None:
            width_ok = new_width <= old_width * (1 + WIDTH_TOLERANCE_REL)
            width_note = f"{new_width:.2%} (было {old_width:.2%}, допуск ×{1+WIDTH_TOLERANCE_REL})"
    checks.append(("coverage", coverage_ok))
    checks.append(("width", width_ok))

    passed = all(ok for _, ok in checks)

    lines += [
        f"_База сравнения: {mode}_",
        "",
        "| Метрика | Было | Стало | OK |",
        "|---|---|---|---|",
        f"| MAPE | {old['mape']:.2%} | {new['mape']:.2%} | {'✅' if mape_ok else '❌'} |",
        f"| MAE | {old['mae'] / 1e6:.2f}M ₸ | {new['mae'] / 1e6:.2f}M ₸ | {'✅' if mae_ok else '❌'} |",
        f"| R² | {old['r2']:.3f} | {new['r2']:.3f} | — |",
    ]
    if bootstrap_note:
        lines.append(f"| _{bootstrap_note}_ |||")
    if coverage_note:
        lines.append(f"| Coverage интервала | — | {coverage_note} | {'✅' if coverage_ok else '❌'} |")
    if width_note:
        lines.append(f"| Ширина интервала | — | {width_note} | {'✅' if width_ok else '❌'} |")
    lines += [
        "",
        "**Вердикт:** " + ("✅ деплоим" if passed else "❌ новая модель хуже — оставляем старую"),
    ]
    report = "\n".join(lines)
    print(report)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
