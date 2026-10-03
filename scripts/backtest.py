#!/usr/bin/env python
"""issue #130: walk-forward стенд — честное сравнение моделей на истории БД.

Идея: вместо одного time-based holdout (как в train.py) прогоняем 6-8
еженедельных «срезов» (folds) назад по времени. Для каждого среза с датой
начала test-недели `T`:

  - train = объявления с first_seen < T - gap, цена — последняя точка
    price_history СТРОГО ДО T - gap (не текущая цена из listings — та
    отражает самый свежий скрейп, а не то, что было известно на момент
    обучения). gap по умолчанию 0: модель переобучена прямо перед неделей;
    --gap-days 14 повторяет прод, где свежие ~10–14 дней уходят в test
    ретрейна и в боевую модель не попадают;
  - test = объявления, впервые увиденные в неделю среза, first_seen в
    [T, T+7д) — цена восстановлена по состоянию на T+7д (конец недели теста,
    это единственная точка, где мы «заглядываем» на неделю вперёд, и то
    только в пределах собственного окна теста, не дальше).

ОДИН КОД С ПРОДОМ. Подготовка строк (krisha.train.prepare_frame: очистка,
подселение, районы по OSM), точечная модель (fit_point_model: early
stopping, финал на всём train) и интервал (fit_quantile_interval +
interval_bounds: своя временная калибровка CQR внутри train) — те же
функции, что в train(). До 10.2026 стенд держал свою копию пайплайна, и
она разошлась с продом: без resolve_zones district/microdistrict_ppsm
строились по сырым районам krisha, точечная модель — 600 деревьев без
early stopping, а последняя неделя перед тестом в неё не попадала вовсе.
Выводы «фича не помогла», сделанные на старом стенде, стоит перепроверить.

purge по fingerprint убирает из train строки, чей отпечаток (та же
квартира, перевыставленная под другим id) всплывает в test — иначе test
частично протекает в train.

Эксперименты (сравниваются парой прогонов и --compare): --point-iterations,
--learning-rate, --depth, --gap-days, --target-mode, --freshness-half-life-days,
--train-window-weeks; --deal arenda — то же для модели аренды.

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
from catboost import Pool

from krisha.config import REPORTS_DIR
from krisha.features import build_features
from krisha.model_spec import SALE, SPECS, ModelSpec, spec_for
from krisha.targets import (
    TARGET_MODES,
    add_target_column,
    build_city_index,
    freshness_weight,
    predict_price,
    target_col,
)
from krisha.train import (
    POINT_ITERATIONS,
    QUANTILE_ITERATIONS,
    baseline_predict,
    building_groups,
    dedup_relistings,
    fit_point_model,
    fit_quantile_interval,
    interval_bounds,
    prepare_frame,
    purge_leaked_train_rows,
)
from krisha.validity import representativeness

logger = logging.getLogger(__name__)

FOLD_WINDOW_DAYS = 7   # неделя, как в issue #130
N_FOLDS_DEFAULT = 8
# issue #158: сколько валидных фолдов нужно, чтобы агрегат имел смысл как
# ВРЕМЕННАЯ оценка. Один-два фолда — это оценка одной-двух конкретных недель,
# а не свойства модели: разброс между неделями сам по себе больше разницы
# между моделями, которую мы пытаемся померить.
MIN_VALID_FOLDS = 3
# Потолки — как в проде (scripts/train.py, train.QUANTILE_ITERATIONS): реальное
# число деревьев режет early stopping. Облегчённые потолки стенда (600/400 без
# early stopping) мерили не ту модель, что работает в проде.
POINT_ITERATIONS_DEFAULT = POINT_ITERATIONS
QUANTILE_ITERATIONS_DEFAULT = QUANTILE_ITERATIONS


# --- Фолды ---------------------------------------------------------------

@dataclass(frozen=True)
class Fold:
    index: int
    train_end: pd.Timestamp     # train: first_seen < train_end (= test_start - gap)
    test_start: pd.Timestamp    # test: first_seen в [test_start, test_end)
    test_end: pd.Timestamp


def make_folds(
    max_ts: pd.Timestamp,
    n_folds: int = N_FOLDS_DEFAULT,
    window_days: int = FOLD_WINDOW_DAYS,
    gap_days: int = 0,
) -> list[Fold]:
    """N еженедельных фолдов, самый свежий — test = последняя неделя данных.

    Фолд i=n_folds-1 (последний): test = [max_ts - window, max_ts).
    Фолд i=0 (самый старый): test = [max_ts - n_folds*window, max_ts - (n_folds-1)*window).
    gap_days — сколько дней перед тестом модель не видит (прод: свежий test ретрейна).
    """
    folds = []
    for i in range(n_folds):
        offset_weeks = n_folds - 1 - i
        test_end = max_ts - pd.Timedelta(days=offset_weeks * window_days)
        test_start = test_end - pd.Timedelta(days=window_days)
        train_end = test_start - pd.Timedelta(days=gap_days)
        folds.append(Fold(index=i, train_end=train_end, test_start=test_start, test_end=test_end))
    return folds


# --- Загрузка данных: сырые листинги + полная история цены ----------------

def load_raw_with_history(db_path: Path | str = SALE.db_path) -> tuple[pd.DataFrame, pd.DataFrame]:
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
    # format="mixed" обязателен: в price_history соседствуют два формата —
    # 'YYYY-MM-DD HH:MM:SS' от миграционного бэкфилла (там DEFAULT
    # datetime('now')) и 'YYYY-MM-DD HH:MM:SS.mmm' от обычной записи (там
    # strftime('%Y-%m-%d %H:%M:%f','now')). Без него pandas выводит ОДИН
    # формат по первому значению и молча превращает все остальные в NaT.
    #
    # Цена ошибки была не теоретической: на проде из 22 475 точек истории
    # выживало 7 050, то есть 69% выбрасывалось — оставалась только когорта
    # первичного краула 11–12.06. Все фолды получали calib=0/test=0 и
    # пропускались «по нехватке данных», хотя данные были. В stats.py этот
    # format="mixed" уже стоял — здесь его просто забыли.
    price_history["observed_at"] = pd.to_datetime(
        price_history["observed_at"], format="mixed", errors="coerce", utc=True
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


# --- Сборка train/test одного фолда ---------------------------------------

def build_fold_data(
    listings: pd.DataFrame, price_history: pd.DataFrame, fold: Fold, spec: ModelSpec = SALE,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Возвращает (train_raw, test_raw, n_purged) для фолда.

    train: first_seen < fold.train_end, цена — по состоянию на fold.train_end
    (что было известно, когда модель учили). test: цена по состоянию на
    fold.test_end (конец собственной недели теста — единственный взгляд
    «вперёд», не дальше границы фолда). Строки готовит prepare_frame — тот же
    шаг, что в train().
    """
    fs = listings["first_seen"]
    ids_train = listings.index[fs < fold.train_end]
    ids_test = listings.index[(fs >= fold.test_start) & (fs < fold.test_end)]

    train_raw = _frame_for_ids(
        listings, listings.loc[ids_train, "id"], asof_prices(price_history, fold.train_end)
    )
    test_raw = _frame_for_ids(
        listings, listings.loc[ids_test, "id"], asof_prices(price_history, fold.test_end)
    )
    train_raw = prepare_frame(train_raw, spec) if len(train_raw) else train_raw
    test_raw = prepare_frame(test_raw, spec) if len(test_raw) else test_raw

    # Дедуп перезалитых — как в проде dedup_relistings(df) до сплита, но без
    # last_seen/scraped_at: они отражают состояние БД НА СЕЙЧАС, не на дату
    # фолда, и порядок «свежести» внутри группы подглядывал бы в будущее.
    train_raw = train_raw.drop(columns=[c for c in ("last_seen", "scraped_at") if c in train_raw])
    n_before_dedup = len(train_raw)
    if n_before_dedup:
        train_raw = dedup_relistings(train_raw)
    n_deduped = n_before_dedup - len(train_raw)

    n_purged = 0
    if len(train_raw) and len(test_raw):
        train_raw, n_purged = purge_leaked_train_rows(train_raw, test_raw)
    if n_deduped or n_purged:
        logger.info(
            "fold %d: дедуп -%d, purge fingerprint -%d (train %d -> %d)",
            fold.index, n_deduped, n_purged, n_before_dedup, len(train_raw),
        )
    return train_raw.reset_index(drop=True), test_raw.reset_index(drop=True), n_purged


# --- Обучение и предсказание одного фолда ---------------------------------

def run_fold(
    train_raw: pd.DataFrame,
    test_raw: pd.DataFrame,
    spec: ModelSpec = SALE,
    point_iterations: int = POINT_ITERATIONS_DEFAULT,
    quantile_iterations: int = QUANTILE_ITERATIONS_DEFAULT,
    learning_rate: float = 0.05,
    depth: int = 8,
    target_mode: str = "price",
    freshness_half_life_days: float | None = None,
    train_window_weeks: int | None = None,
    as_of: pd.Timestamp | None = None,
    cb_params: dict | None = None,
    seed: int = 42,
    with_interval: bool = True,
) -> pd.DataFrame | None:
    """Обучает точечную и квантильную модели прод-кодом на train_raw и
    предсказывает test_raw. Возвращает per-row DataFrame (в .attrs — число
    деревьев и размер калибровки) или None, если фолд слишком мал.

    issue #131 — режимы эксперимента (признаки spec.all_features не меняются):
    - target_mode: "price" (прод, log1p(price)) | "ppsm" (log(price/area)) |
      "index_residual" (log(price/area) минус лог city_index недели, см.
      krisha.targets) — только у точечной модели; интервал, как в проде, по
      log1p(price). Оценка/coverage всегда в ₸, сравнение режимов честное.
    - freshness_half_life_days: вес train-строк 0.5**(age_days/half_life),
      age_days относительно `as_of` (граница фолда). None — без весов.
    - train_window_weeks: train — только последние N недель перед `as_of`.
    learning_rate/depth/point_iterations — гиперпараметры точечной модели.
    """
    if len(train_raw) < 30 or len(test_raw) < 5:
        return None
    if as_of is None:
        as_of = pd.to_datetime(train_raw["first_seen"], utc=True, errors="coerce").max()

    if train_window_weeks is not None:
        cutoff = pd.Timestamp(as_of) - pd.Timedelta(weeks=train_window_weeks)
        fs = pd.to_datetime(train_raw["first_seen"], utc=True, errors="coerce")
        train_raw = train_raw[fs >= cutoff].reset_index(drop=True)
        if len(train_raw) < 30:
            return None

    index_ref = build_city_index(train_raw) if target_mode == "index_residual" else None
    tcol = target_col(target_mode)

    def target_fn(frame: pd.DataFrame):
        return add_target_column(frame, target_mode, index_ref=index_ref)[0][tcol]

    weight_fn = None
    if freshness_half_life_days is not None:
        def weight_fn(frame: pd.DataFrame):
            return freshness_weight(frame["first_seen"], as_of, freshness_half_life_days)

    point = fit_point_model(
        train_raw, spec, point_iterations, learning_rate=learning_rate, depth=depth,
        target_fn=target_fn, weight_fn=weight_fn, verbose=False,
        cb_params=cb_params, seed=seed,
    )
    test_df = build_features(test_raw, ppsm_maps=point.ppsm_maps, spatial_ref=point.spatial_ref)
    test_pool = Pool(test_df[spec.all_features], cat_features=list(spec.cat_features))
    y_point_test = predict_price(
        point.model.predict(test_pool), test_df["area"], target_mode, index_ref=index_ref,
    )
    y_base_test = baseline_predict(point.train_df, test_df)

    if with_interval:
        quantile_model, scale, quantile_meta = fit_quantile_interval(train_raw, spec, quantile_iterations)
        price_lo, price_hi = interval_bounds(quantile_model, scale, test_df, y_point_test, spec)
    else:  # эксперимент только про точку — интервал (~40% CPU) не считаем
        quantile_meta = {"quantile_best_iterations": None, "n_calib": None}
        price_lo = price_hi = np.full(len(test_df), np.nan)

    out = pd.DataFrame({
        "listing_id": test_df["id"].to_numpy() if "id" in test_df else np.arange(len(test_df)),
        "first_seen": test_df["first_seen"].to_numpy(),
        "district": test_df["district"].to_numpy(),
        "is_new_building": test_df["is_new_building"].to_numpy(),
        "price_true": test_df["price"].to_numpy(),
        "price_pred": y_point_test,
        "price_baseline": y_base_test,
        "price_lo": price_lo,
        "price_hi": price_hi,
        # кластер для бутстрепа сравнения: строки одного дома не независимы
        "building": building_groups(test_df).astype(str).to_numpy(),
    })
    out.attrs.update({
        "best_iterations": point.best_iterations,
        "quantile_best_iterations": quantile_meta["quantile_best_iterations"],
        "n_calib": quantile_meta["n_calib"],
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
    db_path: Path | str | None = None,
    n_folds: int = N_FOLDS_DEFAULT,
    window_days: int = FOLD_WINDOW_DAYS,
    point_iterations: int = POINT_ITERATIONS_DEFAULT,
    quantile_iterations: int = QUANTILE_ITERATIONS_DEFAULT,
    target_mode: str = "price",
    freshness_half_life_days: float | None = None,
    train_window_weeks: int | None = None,
    spec: ModelSpec = SALE,
    gap_days: int = 0,
    learning_rate: float = 0.05,
    depth: int = 8,
    cb_params: dict | None = None,
    seed: int = 42,
    with_interval: bool = True,
    skip_invalid_folds: bool = False,
) -> tuple[pd.DataFrame, dict]:
    if target_mode not in TARGET_MODES:
        raise ValueError(f"target_mode должен быть один из {TARGET_MODES}, получено {target_mode!r}")
    listings, price_history = load_raw_with_history(db_path or spec.db_path)
    max_ts = listings["first_seen"].max()
    folds = make_folds(max_ts, n_folds=n_folds, window_days=window_days, gap_days=gap_days)
    config = {
        "deal": spec.deal,
        "gap_days": gap_days,
        "point_iterations": point_iterations,
        "quantile_iterations": quantile_iterations,
        "learning_rate": learning_rate,
        "depth": depth,
        "target_mode": target_mode,
        "freshness_half_life_days": freshness_half_life_days,
        "train_window_weeks": train_window_weeks,
        "cb_params": cb_params or {},
        "seed": seed,
        "with_interval": with_interval,
        "max_first_seen": str(max_ts),
    }

    all_preds = []
    fold_summaries = []
    skipped = 0
    invalid = 0
    for fold in folds:
        train_raw, test_raw, n_purged = build_fold_data(listings, price_history, fold, spec)
        if skip_invalid_folds and len(test_raw):
            # валидность зависит только от теста — проверяем ДО обучения
            if not representativeness(test_raw, train_raw)["representative"]:
                invalid += 1
                logger.warning("fold %d невалиден — пропущен без обучения", fold.index)
                continue
        preds = run_fold(
            train_raw, test_raw, spec,
            point_iterations=point_iterations, quantile_iterations=quantile_iterations,
            learning_rate=learning_rate, depth=depth,
            target_mode=target_mode, freshness_half_life_days=freshness_half_life_days,
            train_window_weeks=train_window_weeks, as_of=fold.train_end,
            cb_params=cb_params, seed=seed, with_interval=with_interval,
        )
        if preds is None:
            skipped += 1
            logger.warning(
                "fold %d пропущен: недостаточно данных (train=%d test=%d)",
                fold.index, len(train_raw), len(test_raw),
            )
            continue
        preds["fold"] = fold.index
        preds["test_start"] = fold.test_start
        preds["test_end"] = fold.test_end
        # issue #158: фолд валиден, только если его тест похож на генеральную
        # выборку. Иначе rolling-origin померяет не обобщение во времени, а
        # перенос между районами: сбор шёл по алфавиту с лимитом 1000/день,
        # поэтому «неделя» в июле означала «район». Невалидный фолд считается
        # и показывается, но НЕ входит в агрегат.
        # Сравниваем с train фолда, подготовленным тем же prepare_frame: сырой
        # listings (районы krisha до OSM-починки, лоты без деталей) отличался
        # от любой недели по районам, и все 8 фолдов уходили в «невалидные».
        fold_validity = representativeness(test_raw, train_raw)
        preds["fold_valid"] = fold_validity["representative"]
        fold_stats = summarize(preds)["overall"]
        fold_stats.update({
            "fold": fold.index, "test_start": str(fold.test_start), "test_end": str(fold.test_end),
            "n_train": len(train_raw), "n_calib": preds.attrs.get("n_calib"), "n_purged": n_purged,
            "best_iterations": preds.attrs.get("best_iterations"),
            "quantile_best_iterations": preds.attrs.get("quantile_best_iterations"),
            "valid": fold_validity["representative"],
            "worst_tvd": fold_validity["worst_tvd"],
        })
        fold_summaries.append(fold_stats)
        if not fold_validity["representative"]:
            invalid += 1
            logger.warning(
                "fold %d НЕВАЛИДЕН: worst TVD %.3f > %s — тест не представляет "
                "выборку, в агрегат не берём",
                fold.index, fold_validity["worst_tvd"], fold_validity["threshold"],
            )
        # Невалидный фолд не входит в агрегат (issue #158), но его per-row
        # предикты сохраняем: парное сравнение двух прогонов (--compare,
        # join по fold+listing_id) сравнивает модели на ОДНИХ И ТЕХ ЖЕ
        # строках — перекос состава теста сокращается в разности, поэтому
        # для A/B-сравнения такие строки пригодны, в отличие от абсолютной
        # «временной оценки». Флаг fold_valid остаётся в CSV.
        all_preds.append(preds)

    # issue #158: агрегат публикуем только если валидных фолдов достаточно.
    # Отказ с диагностикой — это КОРРЕКТНЫЙ вывод методики на текущих данных,
    # а не её провал: на сборе 02.07–13.07 валидных фолдов ноль, и число,
    # выданное «хотя бы для справки», приживётся в обсуждениях, а оговорка нет.
    valid_preds = [p for p in all_preds if bool(p["fold_valid"].iloc[0])]
    if not valid_preds:
        report = {
            "overall": None,
            "temporal_estimate": None,
            "per_fold": fold_summaries,
            "n_folds_run": 0,
            "n_folds_skipped": skipped,
            "n_folds_invalid": invalid,
            "reason": (
                f"валидных фолдов 0 из {len(folds)} "
                f"(пропущено по нехватке данных: {skipped}, непредставительных: {invalid}) "
                "— временная оценка недоступна"
            ),
            "config": config,
        }
        logger.error("issue #158: %s", report["reason"])
        # per-row предикты (с fold_valid=False) возвращаем ради --compare;
        # агрегат по ним по-прежнему не публикуется.
        combined_all = pd.concat(all_preds, ignore_index=True) if all_preds else pd.DataFrame()
        return combined_all, report

    combined = pd.concat(all_preds, ignore_index=True)
    report = summarize(pd.concat(valid_preds, ignore_index=True))
    if len(valid_preds) < MIN_VALID_FOLDS:
        report["temporal_estimate"] = None
        report["reason"] = (
            f"валидных фолдов {len(valid_preds)} < {MIN_VALID_FOLDS} — агрегат посчитан, "
            "но как временная оценка не годится"
        )
        logger.warning("issue #158: %s", report["reason"])
    report["per_fold"] = fold_summaries
    report["n_folds_run"] = len(valid_preds)
    report["n_folds_invalid"] = invalid
    report["n_folds_skipped"] = skipped
    report["config"] = config
    return combined, report


def _report_markdown(report: dict, label: str) -> str:
    o = report["overall"]
    # issue #158: отказ — законный исход, и отчёт обязан его показывать, а не
    # падать. Пустой агрегат означает, что ни один фолд не прошёл проверку на
    # представительность: на данных 02.07–13.07 каждая «неделя» это отдельный
    # район, и любое число здесь описывало бы перенос между районами.
    if o is None:
        return "\n".join([
            f"### Walk-forward backtest — `{label}`",
            "",
            "**Временная оценка недоступна.**",
            "",
            f"{report.get('reason', 'нет валидных фолдов')}",
            "",
            "**По фолдам:**",
            "",
            "| fold | test_start | n | worst TVD | вердикт |",
            "|---|---|---|---|---|",
            *[
                f"| {f['fold']} | {f['test_start'][:10]} | {f['n']} | "
                f"{f.get('worst_tvd', '?')} | "
                f"{'валиден' if f.get('valid') else 'непредставителен'} |"
                for f in report.get("per_fold", [])
            ],
        ])
    lines = [
        f"### Walk-forward backtest — `{label}`",
        "",
        f"Фолдов валидных: {report['n_folds_run']} "
        f"(пропущено по данным: {report['n_folds_skipped']}, "
        f"непредставительных: {report.get('n_folds_invalid', 0)})",
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

def _cluster_bootstrap_ci(
    delta: np.ndarray, clusters: np.ndarray, n_boot: int = 2000, seed: int = 0,
) -> tuple[float, float]:
    """95% ДИ среднего парной разности с ресемплом по домам (строки одного
    дома зависимы — построчный бутстреп дал бы слишком узкий интервал)."""
    codes, uniq = pd.factorize(pd.Series(clusters))
    sums = np.bincount(codes, weights=delta, minlength=len(uniq))
    counts = np.bincount(codes, minlength=len(uniq))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return float(lo), float(hi)


def compare_runs(
    csv_a: Path | str, csv_b: Path | str, label_a: str = "A", label_b: str = "B",
    include_invalid: bool = False,
) -> str:
    """Парное сравнение двух прогонов на ОДНИХ фолдах (join по fold+listing_id).

    Только валидные фолды (если не include_invalid); ΔMAPE с 95% ДИ кластерного
    бутстрепа по домам и разбивкой по фолдам — разница 0.1 п.п. без интервала
    неотличима от шума сида.
    """
    a = pd.read_csv(csv_a)
    b = pd.read_csv(csv_b)
    joined = a.merge(b, on=["fold", "listing_id"], suffixes=("_a", "_b"))
    if not include_invalid and "fold_valid_a" in joined:
        joined = joined[joined["fold_valid_a"].astype(bool) & joined["fold_valid_b"].astype(bool)]
    if joined.empty:
        raise ValueError("Нет пересечения по (fold, listing_id) — прогоны не на одних фолдах/БД")
    if "test_start_a" in joined and (joined["test_start_a"] != joined["test_start_b"]).any():
        raise ValueError("Фолды с одним номером приходятся на разные недели — база менялась между прогонами")

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
    delta = ape_b - ape_a
    clusters = joined["building_a"].to_numpy() if "building_a" in joined else joined["listing_id"].to_numpy()
    lo, hi = _cluster_bootstrap_ci(delta, clusters)
    verdict = "лучше" if hi < 0 else ("хуже" if lo > 0 else "разница в пределах шума")
    lines += [
        "",
        f"**ΔMAPE {np.mean(delta):+.2%}, 95% ДИ [{lo:+.2%}; {hi:+.2%}]** "
        f"(кластерный бутстреп по домам) — `{label_b}` {verdict}",
        "",
        "| Фолд | n | ΔMAPE |",
        "|---|---|---|",
    ]
    joined = joined.assign(_delta=delta)
    better = 0
    for fold, g in joined.groupby("fold"):
        d = g["_delta"].mean()
        better += d < 0
        lines.append(f"| {fold} | {len(g)} | {d:+.2%} |")
    lines.append(f"\n`{label_b}` лучше в {better} из {joined['fold'].nunique()} фолдов")
    return "\n".join(lines)


def _parse_cb_params(items: list[str]) -> dict:
    """['l2_leaf_reg=10', 'loss_function=MAE'] → {'l2_leaf_reg': 10, 'loss_function': 'MAE'}."""
    out = {}
    for item in items:
        key, _, raw = item.partition("=")
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            out[key.strip()] = raw
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="по умолчанию — база выбранной сделки")
    parser.add_argument("--deal", choices=sorted(SPECS), default="prodazha",
                        help="какую модель проверять: продажа или аренда")
    parser.add_argument("--label", default="current", help="Тег версии пайплайна для имён файлов")
    parser.add_argument("--out", default=str(REPORTS_DIR / "backtest"))
    parser.add_argument("--n-folds", type=int, default=N_FOLDS_DEFAULT)
    parser.add_argument("--window-days", type=int, default=FOLD_WINDOW_DAYS)
    parser.add_argument("--point-iterations", type=int, default=POINT_ITERATIONS_DEFAULT,
                        help="потолок деревьев точечной модели (early stopping режет раньше)")
    parser.add_argument("--quantile-iterations", type=int, default=QUANTILE_ITERATIONS_DEFAULT)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument(
        "--cb-param", action="append", default=[], metavar="KEY=JSON",
        help="любой параметр CatBoost точечной модели, напр. --cb-param l2_leaf_reg=10 "
             "--cb-param loss_function='\"MAE\"' (значение — JSON, иначе строка)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-interval", action="store_true",
                        help="не строить интервал (эксперимент только про точку, ~40%% быстрее)")
    parser.add_argument("--skip-invalid-folds", action="store_true",
                        help="не обучать на непредставительных фолдах (в агрегат они и так не идут)")
    parser.add_argument("--include-invalid", action="store_true",
                        help="--compare: учитывать и невалидные фолды")
    parser.add_argument("--depth", type=int, default=8)
    parser.add_argument(
        "--gap-days", type=int, default=0,
        help="дней перед тестом, которых модель не видит: 0 — переобучена перед неделей, "
             "14 — как прод (свежий test ретрейна в боевую модель не попадает)",
    )
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
        report_md = compare_runs(csv_a, csv_b, label_a, label_b, include_invalid=args.include_invalid)
        print(report_md)
        (out_dir / f"compare_{label_a}_vs_{label_b}.md").write_text(
            report_md, encoding="utf-8"
        )
        return

    combined, report = run_backtest(
        db_path=args.db, n_folds=args.n_folds, window_days=args.window_days,
        point_iterations=args.point_iterations, quantile_iterations=args.quantile_iterations,
        target_mode=args.target_mode, freshness_half_life_days=args.freshness_half_life_days,
        train_window_weeks=args.train_window_weeks, spec=spec_for(args.deal),
        gap_days=args.gap_days, learning_rate=args.learning_rate, depth=args.depth,
        cb_params=_parse_cb_params(args.cb_param), seed=args.seed,
        with_interval=not args.no_interval, skip_invalid_folds=args.skip_invalid_folds,
    )
    csv_path = out_dir / f"{args.label}_predictions.csv"
    combined.to_csv(csv_path, index=False)
    report_json_path = out_dir / f"{args.label}_report.json"
    report_json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    report_md = _report_markdown(report, args.label)
    (out_dir / f"{args.label}_report.md").write_text(report_md, encoding="utf-8")
    print(report_md)
    print(f"\nper-row предикты: {csv_path}")


if __name__ == "__main__":
    main()
