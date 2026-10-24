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
- гейтим покрытие доверительного интервала (coverage_test) и его ширину.
- если рядом лежит models/model_gate_samples.json (пары APE новая/старая
  модель по каждой test-строке — пишет train.py), деградацию MAPE проверяем
  парным бутстрепом разницы средних APE, а не плоским допуском.

Аудит 24.10:
- бутстреп кластерный: ресемплятся целые здания (clusters в samples), а не
  строки — ~65% теста составляют квартиры-близнецы и перевыставления,
  построчный бутстреп считал их независимыми;
- допуск к нижней границе CI — 0.15 п.п. вместо 0.5 п.п. (обоснование у
  BOOTSTRAP_TOLERANCE);
- защита от накопления: каждую неделю «чуть хуже, но в допуске» за месяц
  складывается в заметную деградацию, а гейт видит только прошлую модель.
  Поэтому MAPE сверяется ещё и с лучшей из последних HISTORY_WINDOW записей
  metrics_history.jsonl (мягко — см. history_check);
- ширина интервала старой модели считается на ТОМ ЖЕ test (train.py пишет
  metrics.old_model_interval), а не берётся из старой меты, посчитанной на
  тесте прошлой недели.

Смена методики не должна блокировать навсегда: каждое новое поле (clusters,
old_model_interval, история) необязательно — без него гейт работает в
прежнем, более мягком режиме и пишет об этом предупреждение.

Запуск: python scripts/model_gate.py old_meta.json new_meta.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Допуск на шум: MAE может «дышать» между запусками из-за новых данных.
MAPE_TOLERANCE = 0.005  # +0.5 п.п. абсолютно — fallback, когда бутстреп недоступен
MAE_TOLERANCE_REL = 0.05  # +5% относительно

# issue #106 (доп.): коэффициент допуска на покрытие интервала — 0.80 target
# минус 0.02, и допуск на рост ширины интервала при равном покрытии.
COVERAGE_TOLERANCE = 0.02
WIDTH_TOLERANCE_REL = 0.10

# Парный бутстреп разницы APE (новая - старая): если НИЖНЯЯ граница CI
# разницы средних > допуска — деградация значима, а не шум.
#
# Было 0.5 п.п. На парах прода (model_gate_samples.json ретрейна 27.09,
# 14 818 строк) построчный SD бутстрепа разницы — 0.023 п.п., половина
# 90%-CI — 0.04 п.п.: допуск 0.5 п.п. был ~12 ширин шума, и модель могла
# стать хуже на 0.5 п.п. за ретрейн (7% → 9% за 4 ретрейна) без красного.
# Кластерный бутстреп по зданиям шире построчного в 1.04–1.12 раза на
# локальном тесте (2 827 зданий на 3 777 строк); на прод-тесте с большей
# долей близнецов закладываем до ~1.5× → SD ≈ 0.035 п.п. 0.15 п.п. к нижней
# границе CI — это ~4 SD кластерного шума плюс запас на разброс самого
# обучения (сид/новые данные), который бутстреп теста не видит. Блок
# срабатывает, когда точечная оценка деградации ≳ 0.2 п.п.
BOOTSTRAP_TOLERANCE = 0.0015  # +0.15 п.п.
# Samples без clusters (train.py до аудита 24.10): построчный CI уже
# реального, поэтому допуск вдвое шире — мягкий режим на переходный период.
BOOTSTRAP_TOLERANCE_ROWWISE = 0.003
BOOTSTRAP_N = 2000
BOOTSTRAP_CI = 0.90  # двусторонний; проверяем нижнюю границу

# Защита от накопления деградации: новая MAPE (на своём тесте) не должна быть
# хуже лучшей из последних HISTORY_WINDOW записей истории больше чем на
# max(0.5 п.п., 6% от лучшей). Тесты разных недель разные, поэтому порог с
# запасом: по истории продажи 02.08–18.10 превышение над лучшей из 4
# предыдущих было максимум +0.46 п.п. (13.09→20.09), у аренды +0.36 п.п. —
# ни один реальный ретрейн порог бы не задел. И блокируем, только если новая
# модель ВДОБАВОК не лучше старой на общем тесте — иначе «рекордная» неделя
# с лёгким тестом навсегда закрыла бы дорогу обычным.
HISTORY_WINDOW = 4
HISTORY_TOLERANCE_ABS = 0.005
HISTORY_TOLERANCE_REL = 0.06


def bootstrap_ape_diff(
    ape_new: list[float], ape_old: list[float], clusters: list | None = None, seed: int = 42,
) -> tuple[float, float, float]:
    """Парный бутстреп mean(ape_new) - mean(ape_old). Возвращает (lower, upper, point).

    clusters — метка здания для каждой строки: ресемплятся целые кластеры
    (среднее по строкам выбранных кластеров). None — построчный бутстреп.
    """
    diff = np.asarray(ape_new, dtype=float) - np.asarray(ape_old, dtype=float)
    rng = np.random.default_rng(seed)
    if clusters is None:
        n = len(diff)
        idx = rng.integers(0, n, size=(BOOTSTRAP_N, n))
        boot_means = diff[idx].mean(axis=1)
    else:
        if len(clusters) != len(diff):
            raise ValueError(f"clusters: {len(clusters)} меток на {len(diff)} строк")
        _, inverse = np.unique(np.asarray(clusters, dtype=str), return_inverse=True)
        sums = np.bincount(inverse, weights=diff)
        counts = np.bincount(inverse).astype(float)
        k = len(sums)
        boot_means = np.empty(BOOTSTRAP_N)
        # чанками: матрица BOOTSTRAP_N × k индексов на 20k зданий — 320 МБ
        step = max(1, 2_000_000 // max(k, 1))
        for start in range(0, BOOTSTRAP_N, step):
            stop = min(BOOTSTRAP_N, start + step)
            picked = rng.integers(0, k, size=(stop - start, k))
            boot_means[start:stop] = sums[picked].sum(axis=1) / counts[picked].sum(axis=1)
    tail = (1 - BOOTSTRAP_CI) / 2
    lower = float(np.quantile(boot_means, tail))
    upper = float(np.quantile(boot_means, 1 - tail))
    return lower, upper, float(diff.mean())


def _parse_ts(value) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    # наивные и aware-метки нельзя сравнивать между собой — наивные считаем UTC
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def load_history(path: Path, before: str | None, window: int = HISTORY_WINDOW) -> list[dict]:
    """Последние `window` записей истории строго раньше `before` (trained_at новой).

    train.py дописывает в историю саму новую модель ещё ДО гейта — её запись
    (и всё, что не раньше её trained_at) отбрасываем. Битые строки — мимо.
    """
    if not path.exists():
        return []
    cutoff = _parse_ts(before) if before else None
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(rec.get("trained_at"))
        if not isinstance(rec.get("mape"), int | float) or ts is None:
            continue
        if cutoff is not None and ts >= cutoff:
            continue
        rows.append((ts, rec))
    rows.sort(key=lambda r: r[0])
    return [rec for _, rec in rows[-window:]]


def history_check(new_mape: float, history: list[dict], worse_than_old: bool) -> tuple[bool, str | None]:
    """(ok, заметка). Мягкая проверка накопления — см. HISTORY_WINDOW."""
    if not history:
        return True, None
    best = min(float(r["mape"]) for r in history)
    tol = max(HISTORY_TOLERANCE_ABS, HISTORY_TOLERANCE_REL * best)
    excess = new_mape - best
    over = excess > tol
    ok = not (over and worse_than_old)
    note = (
        f"{new_mape:.2%} против лучшей из {len(history)} последних {best:.2%} "
        f"({excess:+.2%}, допуск +{tol:.2%})"
    )
    if over and not worse_than_old:
        note += " — выше допуска, но на общем тесте новая не хуже старой: пропускаем"
    return ok, note


def main() -> None:
    parser = argparse.ArgumentParser(description="Гейт: новая модель не хуже старой")
    parser.add_argument("old_meta")
    parser.add_argument("new_meta")
    parser.add_argument(
        "--samples", default="models/model_gate_samples.json",
        help="Файл с парными APE (новая/старая модель) для бутстрепа",
    )
    parser.add_argument(
        "--history",
        help="metrics_history.jsonl для защиты от накопления "
             "(по умолчанию — рядом с new_meta)",
    )
    parser.add_argument("--summary", help="Файл для markdown-отчёта (GITHUB_STEP_SUMMARY)")
    args = parser.parse_args()

    new_meta = json.loads(Path(args.new_meta).read_text(encoding="utf-8"))
    new = new_meta["metrics"]["model"]
    new_full = new
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
    # Поведение — предупреждение, а не блокировка (см. историю в git: взводить
    # блокировку не раньше, чем метрика способна погаснуть и 4 ретрейна подряд
    # зелёная).
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
        # Прошлая модель обучена на всех данных и видела начало test-окна —
        # train.py сравнил обе модели только на более свежих строках.
        if new_meta["metrics"].get("model_vs_old"):
            new = new_meta["metrics"]["model_vs_old"]
            rows = new_meta["metrics"].get("old_model_rows")
            mode += f", только строки новее данных старой модели ({rows})"
    else:
        old = old_meta_full["metrics"]["model"]
        mode = "метрики прошлой недели (разные test-выборки — возможен дрейф данных)"

    # --- MAPE/MAE: бутстреп, если есть парные сэмплы, иначе плоский допуск ---
    samples_path = Path(args.samples)
    bootstrap_note = None
    warnings: list[str] = []
    worse_than_old = new["mape"] > old["mape"]
    if old_on_new_test and samples_path.exists():
        try:
            samples = json.loads(samples_path.read_text(encoding="utf-8"))
            clusters = samples.get("clusters")
            lower, upper, point = bootstrap_ape_diff(
                samples["ape_new"], samples["ape_old"], clusters=clusters,
            )
            tolerance = BOOTSTRAP_TOLERANCE if clusters is not None else BOOTSTRAP_TOLERANCE_ROWWISE
            if clusters is None:
                warnings.append(
                    "⚠️ В samples нет кластеров (train.py старой версии) — построчный "
                    f"бутстреп, допуск расширен до {tolerance:.2%}."
                )
            mape_ok = lower <= tolerance
            worse_than_old = point > 0
            kind = "кластерный по зданиям" if clusters is not None else "построчный"
            n_cl = f", зданий {len(set(clusters))}" if clusters is not None else ""
            bootstrap_note = (
                f"Δ APE (новая-старая), {kind} бутстреп {int(BOOTSTRAP_CI*100)}% CI: "
                f"{point:+.2%} [{lower:+.2%}, {upper:+.2%}], допуск нижней границы "
                f"+{tolerance:.2%}{n_cl}"
            )
        except Exception as exc:  # файл битый/несовместимый — fallback на плоский допуск
            mape_ok = new["mape"] <= old["mape"] + MAPE_TOLERANCE
            bootstrap_note = f"бутстреп недоступен ({exc}), плоский допуск"
    else:
        mape_ok = new["mape"] <= old["mape"] + MAPE_TOLERANCE
    mae_ok = new["mae"] <= old["mae"] * (1 + MAE_TOLERANCE_REL)
    checks.append(("mape", mape_ok))
    checks.append(("mae", mae_ok))

    # --- защита от накопления: лучшая из последних HISTORY_WINDOW недель ---
    history_path = Path(args.history) if args.history else Path(args.new_meta).parent / "metrics_history.jsonl"
    history = load_history(history_path, new_meta["metrics"].get("trained_at"))
    history_ok, history_note = history_check(new_full["mape"], history, worse_than_old)
    if not history:
        warnings.append(f"⚠️ История метрик ({history_path}) пуста — проверка накопления пропущена.")
    checks.append(("history", history_ok))

    # --- issue #106: гейт покрытия и ширины доверительного интервала ---
    coverage_ok = width_ok = True
    coverage_note = width_note = None
    if new_interval:
        target = new_interval.get("target_coverage", 0.80)
        coverage = new_interval.get("coverage_test")
        if coverage is not None:
            coverage_ok = coverage >= target - COVERAGE_TOLERANCE
            coverage_note = f"{coverage:.2%} (допуск ≥ {target - COVERAGE_TOLERANCE:.2%})"
        old_same_test = new_meta["metrics"].get("old_model_interval") or {}
        old_width = old_same_test.get("median_width_pct")
        width_basis = "на том же test"
        if old_width is None:
            old_width = old_interval.get("median_width_pct")
            width_basis = "из старой меты, другой test"
            if old_width:
                warnings.append(
                    "⚠️ Ширина старого интервала взята из старой меты (другой test) — "
                    "старой квантильной модели не было рядом со старой точечной."
                )
        new_width = new_interval.get("median_width_pct")
        if old_width and new_width is not None:
            width_ok = new_width <= old_width * (1 + WIDTH_TOLERANCE_REL)
            width_note = (
                f"{new_width:.2%} (было {old_width:.2%} {width_basis}, "
                f"допуск ×{1+WIDTH_TOLERANCE_REL})"
            )
    checks.append(("coverage", coverage_ok))
    checks.append(("width", width_ok))

    passed = all(ok for _, ok in checks)

    lines += warnings + ([""] if warnings else [])
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
    if history_note:
        lines.append(f"| MAPE против истории | — | {history_note} | {'✅' if history_ok else '❌'} |")
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
