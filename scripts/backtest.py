#!/usr/bin/env python
"""issue #130: walk-forward стенд — честное сравнение моделей на истории БД.

Идея: вместо одного time-based holdout (как в train.py) прогоняем 6-8
еженедельных «срезов» (folds) назад по времени. Для каждого среза с датой
начала test-недели `T`:

  - train = объявления с first_seen < (T - 7д), цена — последняя точка
    price_history СТРОГО ДО T (не текущая цена из listings — та отражает
    самый свежий скрейп, а не то, что было известно на момент среза);
  - calib = объявления с first_seen в [T-7д, T) — последняя неделя перед
    срезом, цена тоже восстановлена по состоянию на T;
  - test = объявления, впервые увиденные в неделю среза, first_seen в
    [T, T+7д) — цена восстановлена по состоянию на T+7д (конец недели теста,
    это единственная точка, где мы «заглядываем» на неделю вперёд, и то
    только в пределах собственного окна теста, не дальше).

purge по fingerprint убирает из train+calib строки, чей отпечаток (та же
квартира, перевыставленная под другим id) всплывает в test — иначе test
частично протекает в train. ppsm_maps/spatial_ref строятся только по train
каждого фолда (не train+calib) — так же, как fit-референс для CQR в
train.train_quantile_interval, чтобы calib не видела свою же цену в
district_ppsm/hex_ppsm/knn.

Запуск (обычный прогон одной версии пайплайна):
    python scripts/backtest.py --label current --out reports/backtest

Сравнение двух версий пайплайна (два git-чекаута/ветки, оба на одних и тех
же фолдах по построению — фолды считаются от max(first_seen) в текущей БД):
    # в чекауте A:
    python scripts/backtest.py --label before --out reports/backtest
    # в чекауте B (та же БД!):
    python scripts/backtest.py --label after --out reports/backtest
    # сравнение (после того, как оба .csv собраны в одном месте):
    python scripts/backtest.py --compare reports/backtest/before_predictions.csv \\
        reports/backtest/after_predictions.csv

Полный прогон — при изменениях пайплайна, не еженедельно (см. issue #130).
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool

from krisha.config import DB_PATH, RANDOM_STATE, REPORTS_DIR
from krisha.features import (
    ALL_FEATURES,
    CAT_FEATURES,
    build_features,
    clean,
    compute_ppsm_maps,
)
from krisha.interval import MIN_INTERVAL_WIDTH_LOG, finalize_interval
from krisha.spatial import build_spatial_ref, self_indices_for
from krisha.targets import (
    TARGET_MODES,
    add_target_column,
    build_city_index,
    freshness_weight,
    predict_price,
    target_col,
)
from krisha.train import (
    CQR_SCALE_MAX,
    INTERVAL_TARGET_COVERAGE,
    _fit_multiquantile,
    baseline_predict,
    dedup_relistings,
    purge_leaked_train_rows,
)

logger = logging.getLogger(__name__)

FOLD_WINDOW_DAYS = 7   # неделя, как в issue #130
N_FOLDS_DEFAULT = 8
POINT_ITERATIONS_DEFAULT = 600     # легче прод. train.py (2000) — стенд не еженедельный, но быстрый
QUANTILE_ITERATIONS_DEFAULT = 400  # легче прод. (800), та же логика


# --- Фолды ---------------------------------------------------------------

@dataclass(frozen=True)
class Fold:
    index: int
    calib_start: pd.Timestamp   # train: first_seen < calib_start
    test_start: pd.Timestamp    # calib: first_seen в [calib_start, test_start)
    test_end: pd.Timestamp      # test: first_seen в [test_start, test_end)


def make_folds(
    max_ts: pd.Timestamp, n_folds: int = N_FOLDS_DEFAULT, window_days: int = FOLD_WINDOW_DAYS
) -> list[Fold]:
    """N еженедельных фолдов, самый свежий — test = последняя неделя данных.

    Фолд i=n_folds-1 (последний): test = [max_ts - window, max_ts).
    Фолд i=0 (самый старый): test = [max_ts - n_folds*window, max_ts - (n_folds-1)*window).
    """
    folds = []
    for i in range(n_folds):
        offset_weeks = n_folds - 1 - i
        test_end = max_ts - pd.Timedelta(days=offset_weeks * window_days)
        test_start = test_end - pd.Timedelta(days=window_days)
        calib_start = test_start - pd.Timedelta(days=window_days)
        folds.append(Fold(index=i, calib_start=calib_start, test_start=test_start, test_end=test_end))
    return folds


# --- Загрузка данных: сырые листинги + полная история цены ----------------

def load_raw_with_history(db_path: Path | str = DB_PATH) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Полная таблица listings (без source='user') + вся price_history.

    В отличие от train.load_dataset(): НЕ фильтруем по is_active/delisted_at
    относительно "сейчас" (это NOW-относительный фильтр, для прошлых фолдов
    неверный — лот, снятый вчера, был живым и валидным полгода назад) и НЕ
    трогаем колонку price здесь — она восстанавливается по price_history для
    каждого фолда отдельно (см. asof_prices).
    """
    with sqlite3.connect(db_path) as conn:
        listings = pd.read_sql("SELECT * FROM listings", conn)
        price_history = pd.read_sql(
            "SELECT listing_id, price, observed_at FROM price_history", conn
        )
    if "source" in listings.columns:
        before = len(listings)
        listings = listings[listings["source"] != "user"].reset_index(drop=True)
        if before != len(listings):
            logger.info("Исключено %s user-предиктов из backtest-выборки", before - len(listings))
    if "first_seen" not in listings.columns:
        raise ValueError("В БД нет колонки first_seen — walk-forward backtest невозможен")
    listings["first_seen"] = pd.to_datetime(listings["first_seen"], errors="coerce", utc=True)
    listings = listings.dropna(subset=["first_seen"]).reset_index(drop=True)
    price_history["observed_at"] = pd.to_datetime(
        price_history["observed_at"], errors="coerce", utc=True
    )
    price_history = price_history.dropna(subset=["observed_at"]).reset_index(drop=True)
    return listings, price_history


def asof_prices(price_history: pd.DataFrame, asof: pd.Timestamp) -> pd.Series:
    """Последняя цена каждого listing_id СТРОГО ДО `asof`. index=listing_id."""
    sub = price_history[price_history["observed_at"] < asof]
    if sub.empty:
        return pd.Series(dtype=float)
    idx = sub.groupby("listing_id")["observed_at"].idxmax()
    return sub.loc[idx].set_index("listing_id")["price"].astype(float)


def _frame_for_ids(listings: pd.DataFrame, ids: pd.Index, price_series: pd.Series) -> pd.DataFrame:
    sub = listings[listings["id"].isin(ids)].copy()
    sub["price"] = sub["id"].map(price_series)
    sub = sub.dropna(subset=["price"]).reset_index(drop=True)
    return sub


# --- Сборка train/calib/test одного фолда ---------------------------------

def build_fold_data(
    listings: pd.DataFrame, price_history: pd.DataFrame, fold: Fold
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, int]:
    """Возвращает (train_raw, calib_raw, test_raw, n_purged) для фолда.

    train/calib: цена восстановлена по состоянию на fold.test_start (что
    было известно ДО начала недели теста). test: цена восстановлена по
    состоянию на fold.test_end (конец собственной недели теста — единственный
    взгляд «вперёд», не дальше границы фолда).
    """
    fs = listings["first_seen"]
    ids_train = listings.index[fs < fold.calib_start]
    ids_calib = listings.index[(fs >= fold.calib_start) & (fs < fold.test_start)]
    ids_test = listings.index[(fs >= fold.test_start) & (fs < fold.test_end)]

    price_before_test = asof_prices(price_history, fold.test_start)
    price_by_test_end = asof_prices(price_history, fold.test_end)

    train_raw = _frame_for_ids(listings, listings.loc[ids_train, "id"], price_before_test)
    calib_raw = _frame_for_ids(listings, listings.loc[ids_calib, "id"], price_before_test)
    test_raw = _frame_for_ids(listings, listings.loc[ids_test, "id"], price_by_test_end)

    train_raw = clean(train_raw) if len(train_raw) else train_raw
    calib_raw = clean(calib_raw) if len(calib_raw) else calib_raw
    test_raw = clean(test_raw) if len(test_raw) else test_raw

    # Дедуп перезалитых (train+calib вместе, как в проде dedup_relistings(df)
    # до сплита) — но без last_seen/scraped_at (они отражают состояние БД
    # НА СЕЙЧАС, не на дату фолда): порядок «свежести» внутри группы должен
    # опираться только на first_seen/id, иначе дедуп подглядывает в будущее
    # относительно фолда.
    train_calib = pd.concat([train_raw, calib_raw], ignore_index=True)
    train_calib = train_calib.drop(columns=[c for c in ("last_seen", "scraped_at") if c in train_calib])
    n_before_dedup = len(train_calib)
    train_calib = dedup_relistings(train_calib)
    n_deduped = n_before_dedup - len(train_calib)

    train_calib, n_purged = purge_leaked_train_rows(train_calib, test_raw)
    if n_deduped or n_purged:
        logger.info(
            "fold %d: дедуп -%d, purge fingerprint -%d (train+calib %d -> %d)",
            fold.index, n_deduped, n_purged, n_before_dedup, len(train_calib),
        )

    fs2 = train_calib["first_seen"]
    train_raw2 = train_calib[fs2 < fold.calib_start].reset_index(drop=True)
    calib_raw2 = train_calib[fs2 >= fold.calib_start].reset_index(drop=True)
    return train_raw2, calib_raw2, test_raw, n_purged


# --- Обучение и предсказание одного фолда ---------------------------------

def run_fold(
    train_raw: pd.DataFrame,
    calib_raw: pd.DataFrame,
    test_raw: pd.DataFrame,
    point_iterations: int = POINT_ITERATIONS_DEFAULT,
    quantile_iterations: int = QUANTILE_ITERATIONS_DEFAULT,
    target_mode: str = "price",
    freshness_half_life_days: float | None = None,
    train_window_weeks: int | None = None,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame | None:
    """Обучает point + quantile модели на train_raw (референсы — только по
    train_raw, не train+calib — issue #130 требование), калибрует интервал по
    calib_raw, предсказывает test_raw. Возвращает per-row DataFrame или None,
    если фолд недостаточно полон (мало train/calib/test) для честной оценки.

    issue #131 — три доп. режима эксперимента (ALL_FEATURES не меняются):
    - target_mode: "price" (прод, log1p(price)) | "ppsm" (log(price/area)) |
      "index_residual" (log(price/area) минус лог city_index недели, см.
      krisha.targets). Оценка/coverage всегда в ₸ (обратный переход перед
      метриками), поэтому сравнение режимов честное.
    - freshness_half_life_days: вес train-строк 0.5**(age_days/half_life) —
      age_days относительно `as_of` (граница фолда). None — веса не заданы
      (все строки равны, как раньше).
    - train_window_weeks: обрезает train_raw до последних N недель перед
      `as_of` (None — вся доступная история, как раньше).
    """
    if len(train_raw) < 30 or len(calib_raw) < 5 or len(test_raw) < 5:
        return None
    if as_of is None:
        as_of = pd.to_datetime(train_raw["first_seen"], utc=True, errors="coerce").max()

    if train_window_weeks is not None:
        cutoff = pd.Timestamp(as_of) - pd.Timedelta(weeks=train_window_weeks)
        fs = pd.to_datetime(train_raw["first_seen"], utc=True, errors="coerce")
        train_raw = train_raw[fs >= cutoff].reset_index(drop=True)
        if len(train_raw) < 30:
            return None

    ppsm_maps = compute_ppsm_maps(train_raw)
    spatial_ref = build_spatial_ref(train_raw)
    train_df = build_features(
        train_raw, ppsm_maps=ppsm_maps, spatial_ref=spatial_ref,
        knn_self_indices=self_indices_for(train_raw),
    )
    calib_df = build_features(calib_raw, ppsm_maps=ppsm_maps, spatial_ref=spatial_ref)
    test_df = build_features(test_raw, ppsm_maps=ppsm_maps, spatial_ref=spatial_ref)

    index_ref = build_city_index(train_raw) if target_mode == "index_residual" else None
    tcol = target_col(target_mode)
    train_df, _ = add_target_column(train_df, target_mode, index_ref=index_ref)
    calib_df, _ = add_target_column(calib_df, target_mode, index_ref=index_ref)

    weight = None
    if freshness_half_life_days is not None:
        weight = freshness_weight(train_df["first_seen"], as_of, freshness_half_life_days)

    train_pool = Pool(
        train_df[ALL_FEATURES], train_df[tcol], cat_features=CAT_FEATURES, weight=weight,
    )
    calib_pool = Pool(calib_df[ALL_FEATURES], cat_features=CAT_FEATURES)
    test_pool = Pool(test_df[ALL_FEATURES], cat_features=CAT_FEATURES)

    point_model = CatBoostRegressor(
        iterations=point_iterations, learning_rate=0.05, depth=8,
        loss_function="RMSE", random_seed=RANDOM_STATE, verbose=False,
    )
    point_model.fit(train_pool)
    y_point_test = predict_price(
        point_model.predict(test_pool), test_df["area"], target_mode, index_ref=index_ref,
    )
    y_base_test = baseline_predict(train_df, test_df)

    # issue #132: одна MultiQuantile-модель вместо model_lo/model_hi — тот же
    # метод, что и прод train.py, чтобы стенд честно сравнивал будущий прод.
    quantile_model = _fit_multiquantile(train_pool, quantile_iterations)

    y_cal = calib_df[tcol].to_numpy()
    preds_cal = quantile_model.predict(calib_pool)
    lo_cal, hi_cal = preds_cal[:, 0], preds_cal[:, 1]
    width_cal = np.maximum(hi_cal - lo_cal, MIN_INTERVAL_WIDTH_LOG)
    scores = np.maximum(lo_cal - y_cal, y_cal - hi_cal) / width_cal
    n = len(scores)
    level = min(1.0, np.ceil((n + 1) * INTERVAL_TARGET_COVERAGE) / n)
    scale = min(max(float(np.quantile(scores, level, method="higher")), 0.0), CQR_SCALE_MAX)

    preds_test = quantile_model.predict(test_pool)
    lo_test, hi_test = preds_test[:, 0], preds_test[:, 1]
    width_test = np.maximum(hi_test - lo_test, MIN_INTERVAL_WIDTH_LOG)
    log_lo = lo_test - scale * width_test
    log_hi = hi_test + scale * width_test
    # issue #131: клип теперь в пространстве таргета (может быть log_price ИЛИ
    # log_ppsm[-index]) — граница +-30 остаётся безопасной для expm1/exp в
    # predict_price (все три режима — плавные монотонные функции без насыщения
    # ниже этого диапазона).
    log_lo = np.clip(log_lo, -30.0, 30.0)
    log_hi = np.clip(log_hi, -30.0, 30.0)
    price_lo = predict_price(log_lo, test_df["area"], target_mode, index_ref=index_ref)
    price_hi = predict_price(log_hi, test_df["area"], target_mode, index_ref=index_ref)
    price_lo, price_hi = finalize_interval(y_point_test, price_lo, price_hi)

    y_true = test_df["price"].to_numpy()
    out = pd.DataFrame({
        "listing_id": test_df["id"].to_numpy() if "id" in test_df else np.arange(len(test_df)),
        "first_seen": test_df["first_seen"].to_numpy(),
        "district": test_df["district"].to_numpy(),
        "is_new_building": test_df["is_new_building"].to_numpy(),
        "price_true": y_true,
        "price_pred": y_point_test,
        "price_baseline": y_base_test,
        "price_lo": price_lo,
        "price_hi": price_hi,
    })
    return out


# --- Метрики ---------------------------------------------------------------

def _ape(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    return np.abs(y_pred - y_true) / np.maximum(y_true, 1.0)


def pinball_loss(y_true: np.ndarray, y_quantile_pred: np.ndarray, alpha: float) -> float:
    diff = y_true - y_quantile_pred
    return float(np.mean(np.maximum(alpha * diff, (alpha - 1) * diff)))


def price_quintile(df: pd.DataFrame) -> pd.Series:
    try:
        return pd.qcut(df["price_true"], 5, labels=[f"q{i+1}" for i in range(5)], duplicates="drop")
    except ValueError:
        return pd.Series(["q_all"] * len(df), index=df.index)


def summarize(df: pd.DataFrame) -> dict:
    """MAPE/MdAPE/bias overall + by segment (район × новостройка/вторичка ×
    ценовой квинтиль); coverage/ширина интервала глобально и по худшему
    сегменту; pinball loss q10/q90 (используем финализированные price_lo/hi
    как предсказания квантилей — это то, что реально видит пользователь)."""
    ape = _ape(df["price_true"].to_numpy(), df["price_pred"].to_numpy())
    bias = (df["price_pred"] - df["price_true"]) / df["price_true"].clip(lower=1)
    covered = (df["price_true"] >= df["price_lo"]) & (df["price_true"] <= df["price_hi"])
    mid = np.maximum((df["price_lo"] + df["price_hi"]) / 2.0, 1.0)
    width_pct = (df["price_hi"] - df["price_lo"]) / mid

    overall = {
        "n": int(len(df)),
        "mape": float(np.mean(ape)),
        "mdape": float(np.median(ape)),
        "bias": float(np.mean(bias)),
        "coverage": float(np.mean(covered)),
        "median_width_pct": float(np.median(width_pct)),
        "pinball_q10": pinball_loss(df["price_true"].to_numpy(), df["price_lo"].to_numpy(), 0.10),
        "pinball_q90": pinball_loss(df["price_true"].to_numpy(), df["price_hi"].to_numpy(), 0.90),
    }

    seg = df.copy()
    seg["building_kind"] = np.where(seg["is_new_building"] == 1, "novostroika", "vtorichka")
    seg["price_quintile"] = price_quintile(seg)
    segments = []
    for keys, g in seg.groupby(["district", "building_kind", "price_quintile"], observed=True):
        if len(g) < 5:
            continue
        g_ape = _ape(g["price_true"].to_numpy(), g["price_pred"].to_numpy())
        g_bias = (g["price_pred"] - g["price_true"]) / g["price_true"].clip(lower=1)
        g_covered = (g["price_true"] >= g["price_lo"]) & (g["price_true"] <= g["price_hi"])
        g_mid = np.maximum((g["price_lo"] + g["price_hi"]) / 2.0, 1.0)
        g_width = (g["price_hi"] - g["price_lo"]) / g_mid
        segments.append({
            "district": keys[0], "building_kind": keys[1], "price_quintile": str(keys[2]),
            "n": int(len(g)), "mape": float(np.mean(g_ape)), "mdape": float(np.median(g_ape)),
            "bias": float(np.mean(g_bias)), "coverage": float(np.mean(g_covered)),
            "median_width_pct": float(np.median(g_width)),
        })
    segments.sort(key=lambda s: -s["mape"])
    worst_segment = segments[0] if segments else None
    worst_coverage_segment = min(segments, key=lambda s: s["coverage"]) if segments else None

    return {
        "overall": overall,
        "worst_mape_segment": worst_segment,
        "worst_coverage_segment": worst_coverage_segment,
        "segments": segments,
    }


# --- Основной прогон --------------------------------------------------------

def run_backtest(
    db_path: Path | str = DB_PATH,
    n_folds: int = N_FOLDS_DEFAULT,
    window_days: int = FOLD_WINDOW_DAYS,
    point_iterations: int = POINT_ITERATIONS_DEFAULT,
    quantile_iterations: int = QUANTILE_ITERATIONS_DEFAULT,
    target_mode: str = "price",
    freshness_half_life_days: float | None = None,
    train_window_weeks: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    if target_mode not in TARGET_MODES:
        raise ValueError(f"target_mode должен быть один из {TARGET_MODES}, получено {target_mode!r}")
    listings, price_history = load_raw_with_history(db_path)
    max_ts = listings["first_seen"].max()
    folds = make_folds(max_ts, n_folds=n_folds, window_days=window_days)

    all_preds = []
    fold_summaries = []
    skipped = 0
    for fold in folds:
        train_raw, calib_raw, test_raw, n_purged = build_fold_data(listings, price_history, fold)
        preds = run_fold(
            train_raw, calib_raw, test_raw,
            point_iterations=point_iterations, quantile_iterations=quantile_iterations,
            target_mode=target_mode, freshness_half_life_days=freshness_half_life_days,
            train_window_weeks=train_window_weeks, as_of=fold.calib_start,
        )
        if preds is None:
            skipped += 1
            logger.warning(
                "fold %d пропущен: недостаточно данных (train=%d calib=%d test=%d)",
                fold.index, len(train_raw), len(calib_raw), len(test_raw),
            )
            continue
        preds["fold"] = fold.index
        preds["test_start"] = fold.test_start
        preds["test_end"] = fold.test_end
        all_preds.append(preds)
        fold_stats = summarize(preds)["overall"]
        fold_stats.update({
            "fold": fold.index, "test_start": str(fold.test_start), "test_end": str(fold.test_end),
            "n_train": len(train_raw), "n_calib": len(calib_raw), "n_purged": n_purged,
        })
        fold_summaries.append(fold_stats)

    if not all_preds:
        raise RuntimeError("Ни один фолд не набрал достаточно данных — проверьте БД/n_folds")

    combined = pd.concat(all_preds, ignore_index=True)
    report = summarize(combined)
    report["per_fold"] = fold_summaries
    report["n_folds_run"] = len(all_preds)
    report["n_folds_skipped"] = skipped
    report["config"] = {
        "target_mode": target_mode,
        "freshness_half_life_days": freshness_half_life_days,
        "train_window_weeks": train_window_weeks,
    }
    return combined, report


def _report_markdown(report: dict, label: str) -> str:
    o = report["overall"]
    lines = [
        f"### Walk-forward backtest — `{label}`",
        "",
        f"Фолдов: {report['n_folds_run']} (пропущено: {report['n_folds_skipped']})",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| n (test, все фолды) | {o['n']} |",
        f"| MAPE | {o['mape']:.2%} |",
        f"| MdAPE | {o['mdape']:.2%} |",
        f"| Bias (mean, знак = переоценка) | {o['bias']:+.2%} |",
        f"| Coverage интервала | {o['coverage']:.2%} |",
        f"| Медианная ширина интервала | {o['median_width_pct']:.2%} |",
        f"| Pinball q10 | {o['pinball_q10']:,.0f} |",
        f"| Pinball q90 | {o['pinball_q90']:,.0f} |",
        "",
        "**По фолдам:**",
        "",
        "| fold | test_start | n | MAPE | coverage |",
        "|---|---|---|---|---|",
    ]
    for f in report["per_fold"]:
        lines.append(
            f"| {f['fold']} | {f['test_start'][:10]} | {f['n']} | {f['mape']:.2%} | {f['coverage']:.2%} |"
        )
    if report["worst_mape_segment"]:
        s = report["worst_mape_segment"]
        lines += [
            "",
            f"**Худший сегмент по MAPE:** {s['district']} / {s['building_kind']} / "
            f"{s['price_quintile']} — MAPE {s['mape']:.2%} (n={s['n']})",
        ]
    if report["worst_coverage_segment"]:
        s = report["worst_coverage_segment"]
        lines += [
            f"**Худший сегмент по coverage:** {s['district']} / {s['building_kind']} / "
            f"{s['price_quintile']} — coverage {s['coverage']:.2%} (n={s['n']})",
        ]
    return "\n".join(lines)


# --- Сравнение двух прогонов -------------------------------------------------

def compare_runs(csv_a: Path | str, csv_b: Path | str, label_a: str = "A", label_b: str = "B") -> str:
    """Парное сравнение двух прогонов на ОДНИХ фолдах (join по fold+listing_id)."""
    a = pd.read_csv(csv_a)
    b = pd.read_csv(csv_b)
    joined = a.merge(b, on=["fold", "listing_id"], suffixes=("_a", "_b"))
    if joined.empty:
        raise ValueError("Нет пересечения по (fold, listing_id) — прогоны не на одних фолдах/БД")

    ape_a = _ape(joined["price_true_a"].to_numpy(), joined["price_pred_a"].to_numpy())
    ape_b = _ape(joined["price_true_b"].to_numpy(), joined["price_pred_b"].to_numpy())
    cov_a = (joined["price_true_a"] >= joined["price_lo_a"]) & (joined["price_true_a"] <= joined["price_hi_a"])
    cov_b = (joined["price_true_b"] >= joined["price_lo_b"]) & (joined["price_true_b"] <= joined["price_hi_b"])
    pin10_a = pinball_loss(joined["price_true_a"].to_numpy(), joined["price_lo_a"].to_numpy(), 0.10)
    pin10_b = pinball_loss(joined["price_true_b"].to_numpy(), joined["price_lo_b"].to_numpy(), 0.10)
    pin90_a = pinball_loss(joined["price_true_a"].to_numpy(), joined["price_hi_a"].to_numpy(), 0.90)
    pin90_b = pinball_loss(joined["price_true_b"].to_numpy(), joined["price_hi_b"].to_numpy(), 0.90)

    lines = [
        f"### Backtest сравнение: `{label_a}` → `{label_b}`",
        "",
        f"Спаренных строк: {len(joined)} (из {len(a)} в {label_a}, {len(b)} в {label_b})",
        "",
        "| Метрика | " + label_a + " | " + label_b + " | Δ |",
        "|---|---|---|---|",
        f"| MAPE | {np.mean(ape_a):.2%} | {np.mean(ape_b):.2%} | {np.mean(ape_b) - np.mean(ape_a):+.2%} |",
        f"| MdAPE | {np.median(ape_a):.2%} | {np.median(ape_b):.2%} | {np.median(ape_b) - np.median(ape_a):+.2%} |",
        f"| Coverage | {np.mean(cov_a):.2%} | {np.mean(cov_b):.2%} | {np.mean(cov_b) - np.mean(cov_a):+.2%} |",
        f"| Pinball q10 | {pin10_a:,.0f} | {pin10_b:,.0f} | {pin10_b - pin10_a:+,.0f} |",
        f"| Pinball q90 | {pin90_a:,.0f} | {pin90_b:,.0f} | {pin90_b - pin90_a:+,.0f} |",
    ]
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--label", default="current", help="Тег версии пайплайна для имён файлов")
    parser.add_argument("--out", default=str(REPORTS_DIR / "backtest"))
    parser.add_argument("--n-folds", type=int, default=N_FOLDS_DEFAULT)
    parser.add_argument("--window-days", type=int, default=FOLD_WINDOW_DAYS)
    parser.add_argument("--point-iterations", type=int, default=POINT_ITERATIONS_DEFAULT)
    parser.add_argument("--quantile-iterations", type=int, default=QUANTILE_ITERATIONS_DEFAULT)
    parser.add_argument(
        "--target-mode", choices=TARGET_MODES, default="price",
        help="issue #131: таргет точечной/квантильной модели (см. krisha.targets)",
    )
    parser.add_argument(
        "--freshness-half-life-days", type=float, default=None,
        help="issue #131: вес train-строк 0.5**(age_days/half_life); по умолчанию без весов",
    )
    parser.add_argument(
        "--train-window-weeks", type=int, default=None,
        help="issue #131: обрезать train до последних N недель перед фолдом; по умолчанию вся история",
    )
    parser.add_argument(
        "--compare", nargs=2, metavar=("CSV_A", "CSV_B"),
        help="Сравнить два готовых *_predictions.csv вместо нового прогона",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.compare:
        csv_a, csv_b = args.compare
        label_a = Path(csv_a).stem.replace("_predictions", "")
        label_b = Path(csv_b).stem.replace("_predictions", "")
        report_md = compare_runs(csv_a, csv_b, label_a, label_b)
        print(report_md)
        (out_dir / f"compare_{label_a}_vs_{label_b}.md").write_text(report_md)
        return

    combined, report = run_backtest(
        db_path=args.db, n_folds=args.n_folds, window_days=args.window_days,
        point_iterations=args.point_iterations, quantile_iterations=args.quantile_iterations,
        target_mode=args.target_mode, freshness_half_life_days=args.freshness_half_life_days,
        train_window_weeks=args.train_window_weeks,
    )
    csv_path = out_dir / f"{args.label}_predictions.csv"
    combined.to_csv(csv_path, index=False)
    report_json_path = out_dir / f"{args.label}_report.json"
    report_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    report_md = _report_markdown(report, args.label)
    (out_dir / f"{args.label}_report.md").write_text(report_md)
    print(report_md)
    print(f"\nper-row предикты: {csv_path}")


if __name__ == "__main__":
    main()
