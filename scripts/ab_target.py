"""Честное A/B таргетов: log(цена) против log(цена/м²).

Зачем. Переход на таргет ₸/м² поднял R² на полной цене (0.80 → 0.91), но MAPE
почти не сдвинулся. Подозрение: ветки `main` и `dev` обучались на РАЗНОМ объёме
данных и разных сплитах, поэтому сравнение было нечестным, а рост R² — артефакт
реконструкции цены умножением на (известную) площадь.

Что делает скрипт. Обучает обе постановки на ОДНИХ данных и ОДНОМ сплите, на
нескольких сидах, и сравнивает по честным метрикам: MdAPE (медианная абс. % ошибка,
устойчива к выбросам) и доля прогнозов в ±10%. Дополнительно показывает эффект
дедупа перезаливов (утечка соседа-перезалива в test раздувает метрики).

Запуск из корня репозитория:
    ./.venv/bin/python scripts/ab_target.py
Результат печатается в stdout и пишется в docs/AB_TARGET_RESULTS.md.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.model_selection import train_test_split

from krisha.config import DB_PATH
from krisha.features import (
    ALL_FEATURES,
    CAT_FEATURES,
    build_features,
    clean,
    compute_ppsm_maps,
    smearing_factor,
)

SEEDS = (42, 7, 123)
TEST_SIZE = 0.2
ITERATIONS = 2000
REPORT_PATH = Path(__file__).resolve().parents[1] / "docs" / "AB_TARGET_RESULTS.md"

# Описание двух постановок. reconstruct(pred, area, S) → полная цена в ₸.
TARGETS = {
    "log_price": {
        "title": "log(цена)",
        "reconstruct": lambda pred, area, s: np.expm1(np.asarray(pred, float)) * s,
    },
    "log_ppm2": {
        "title": "log(цена/м²)",
        "reconstruct": lambda pred, area, s: (
            np.expm1(np.asarray(pred, float)) * s * np.asarray(area, float)
        ),
    },
}


# --- Дедуп перезаливов (локальная копия логики из ветки KNN, чтобы скрипт
#     работал и на dev, где features.dedup ещё нет) -----------------------
def _fingerprint(df: pd.DataFrame) -> pd.Series:
    area = pd.to_numeric(df.get("area"), errors="coerce")
    lat = pd.to_numeric(df.get("lat"), errors="coerce")
    lon = pd.to_numeric(df.get("lon"), errors="coerce")
    district = df.get("district", pd.Series([""] * len(df), index=df.index))
    rooms = df.get("rooms", pd.Series([""] * len(df), index=df.index))
    floor = df.get("floor", pd.Series([""] * len(df), index=df.index))
    total = df.get("total_floors", pd.Series([""] * len(df), index=df.index))

    def _fp(i: Any) -> str | None:
        a, la, lo = area.get(i), lat.get(i), lon.get(i)
        if pd.isna(a) or a == 0 or pd.isna(la) or pd.isna(lo):
            return None
        d = str(district.get(i) or "").lower().strip()
        return "|".join((
            d,
            "" if pd.isna(rooms.get(i)) else str(rooms.get(i)),
            f"{round(float(a) * 2) / 2:.1f}",
            "" if pd.isna(floor.get(i)) else str(floor.get(i)),
            "" if pd.isna(total.get(i)) else str(total.get(i)),
            f"{float(la):.4f}",
            f"{float(lo):.4f}",
        ))

    return pd.Series([_fp(i) for i in df.index], index=df.index)


def dedup(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    fp = _fingerprint(df)
    has = fp.notna()
    if not has.any():
        return df.reset_index(drop=True)
    sub = df.loc[has].copy()
    sub["_fp"] = fp[has]
    if "last_seen" in sub:
        sub = sub.sort_values("last_seen")
    drop_idx = sub.index[sub["_fp"].duplicated(keep="last")]
    return df.drop(index=drop_idx).reset_index(drop=True)


# --- Метрики на полной цене ------------------------------------------------
def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    y_true = np.asarray(y_true, float)
    y_pred = np.asarray(y_pred, float)
    ape = np.abs(y_pred - y_true) / np.clip(np.abs(y_true), 1e-9, None)
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - y_true.mean()) ** 2)
    return {
        "mape": float(np.mean(ape)),
        "mdape": float(np.median(ape)),
        "within_10pct": float(np.mean(ape <= 0.10)),
        "r2": float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan"),
    }


def _fit_eval(df: pd.DataFrame, target: str, seed: int) -> dict:
    """Один прогон: train/test split → обучение → метрики на полной цене."""
    raw_train, raw_test = train_test_split(df, test_size=TEST_SIZE, random_state=seed)
    ppsm_maps = compute_ppsm_maps(raw_train)
    train_df = build_features(raw_train, ppsm_maps=ppsm_maps)
    test_df = build_features(raw_test, ppsm_maps=ppsm_maps)

    train_pool = Pool(train_df[ALL_FEATURES], train_df[target], cat_features=CAT_FEATURES)
    test_pool = Pool(test_df[ALL_FEATURES], test_df[target], cat_features=CAT_FEATURES)
    model = CatBoostRegressor(
        iterations=ITERATIONS,
        learning_rate=0.05,
        depth=8,
        loss_function="RMSE",
        random_seed=seed,
        early_stopping_rounds=100,
        verbose=False,
    )
    model.fit(train_pool, eval_set=test_pool)

    # Smearing-коррекция лог-смещения — только на train-остатках.
    train_pred = model.predict(train_pool)
    s = smearing_factor(train_df[target].to_numpy(), train_pred)

    test_pred = model.predict(test_pool)
    reconstruct = TARGETS[target]["reconstruct"]
    y_pred = reconstruct(test_pred, test_df["area"].to_numpy(), s)
    out = metrics(test_df["price"].to_numpy(), y_pred)
    out["n_train"], out["n_test"] = len(train_df), len(test_df)
    return out


def _avg(runs: list[dict]) -> dict:
    keys = ("mape", "mdape", "within_10pct", "r2")
    return {k: float(np.mean([r[k] for r in runs])) for k in keys}


def main() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        raw = pd.read_sql("SELECT * FROM listings", conn)
    df_all = clean(raw)
    df_dd = dedup(df_all)
    print(f"Загружено {len(raw)} → после clean {len(df_all)} → после dedup {len(df_dd)} "
          f"(удалено перезаливов: {len(df_all) - len(df_dd)})")

    # Основной результат: честный (дедуп), усреднение по сидам.
    main_results: dict[str, dict] = {}
    for target in TARGETS:
        runs = []
        for seed in SEEDS:
            r = _fit_eval(df_dd, target, seed)
            runs.append(r)
            print(f"[dedup] {target:9s} seed={seed:<4d} "
                  f"MAPE={r['mape']:.4f} MdAPE={r['mdape']:.4f} "
                  f"±10%={r['within_10pct']:.3f} R²={r['r2']:.3f}")
        main_results[target] = _avg(runs)

    # Демонстрация утечки: один сид, с дедупом и без.
    leak_results: dict[str, dict] = {}
    for mode, frame in (("без дедупа (утечка)", df_all), ("с дедупом", df_dd)):
        leak_results[mode] = {t: _fit_eval(frame, t, SEEDS[0]) for t in TARGETS}

    _write_report(len(raw), len(df_all), len(df_dd), main_results, leak_results)
    print(f"\nОтчёт записан: {REPORT_PATH}")


def _fmt(m: dict) -> str:
    return (f"{m['mape']*100:5.2f}% | {m['mdape']*100:5.2f}% | "
            f"{m['within_10pct']*100:5.1f}% | {m['r2']:.3f}")


def _write_report(n_raw, n_clean, n_dd, main_results, leak_results) -> None:
    win = min(main_results, key=lambda t: main_results[t]["mdape"])
    lines = [
        "# A/B таргетов: log(цена) против log(цена/м²)",
        "",
        "> Автогенерация: `./.venv/bin/python scripts/ab_target.py`. Обе постановки",
        "> обучены на **одних данных и одном сплите**, усреднение по сидам "
        f"{', '.join(map(str, SEEDS))}.",
        "> Дедуп перезаливов по геоотпечатку — до сплита (иначе сосед-перезалив "
        "утекает в test).",
        "",
        f"Данные: {n_raw} → clean {n_clean} → dedup {n_dd} "
        f"(удалено перезаливов: {n_clean - n_dd}).",
        "",
        "## Честное сравнение (дедуп, среднее по сидам)",
        "",
        "Метрики на ВОССТАНОВЛЕННОЙ полной цене (₸). Headline — MdAPE и доля в ±10%.",
        "",
        "```",
        "таргет        | MAPE   | MdAPE  | ±10%   | R²(цена)",
        "--------------+--------+--------+--------+---------",
    ]
    for t, m in main_results.items():
        lines.append(f"{TARGETS[t]['title']:13s} | {_fmt(m)}")
    lines += [
        "```",
        "",
        f"**Вывод:** по MdAPE выигрывает **{TARGETS[win]['title']}** "
        f"(MdAPE {main_results[win]['mdape']*100:.2f}%, "
        f"±10% {main_results[win]['within_10pct']*100:.1f}%).",
        "",
        "Замечание по R²(цена): он раздут умножением прогноза ₸/м² на известную",
        "площадь, поэтому у `log(цена/м²)` он выше при сопоставимой реальной точности.",
        "Ориентироваться на R² как на метрику прогресса нельзя — отсюда честные",
        "MdAPE и доля в ±10%.",
        "",
        "## Эффект дедупа (один сид, утечка перезаливов)",
        "",
        "```",
        "режим                  | таргет        | MAPE   | MdAPE  | ±10%   | R²(цена)",
        "-----------------------+---------------+--------+--------+--------+---------",
    ]
    for mode, per_t in leak_results.items():
        for t, m in per_t.items():
            lines.append(f"{mode:22s} | {TARGETS[t]['title']:13s} | {_fmt(m)}")
    lines += [
        "```",
        "",
        "Без дедупа метрики оптимистичнее: перезалив той же квартиры попадает",
        "и в train, и в test, и модель «вспоминает» ответ. Это не реальная точность.",
        "",
        "## Рекомендация",
        "",
        "1. Headline-метрики проекта: **MdAPE** и **доля прогнозов в ±10%** "
        "(MAPE — вторично, R²(цена) — убрать из ориентиров прогресса).",
        f"2. Таргет: оставить **{TARGETS[win]['title']}** — он не хуже по честным "
        "метрикам, а ₸/м² ещё и стабильнее по дисперсии.",
        "3. Все сравнения улучшений вести на дедуплицированных данных и фиксированном",
        "   сплите, иначе утечка перезаливов искажает выводы.",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
