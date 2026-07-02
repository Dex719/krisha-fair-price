"""Обучение CatBoost на log(price) + сравнение с baseline + SHAP-отчёт.

Запуск: `python scripts/train.py`. Результат:
- models/model.cbm + models/model_meta.json (метрики, список фичей)
- reports/shap_summary.png
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
from sklearn.model_selection import GroupShuffleSplit

from krisha.config import (
    DB_PATH,
    MODEL_HI_PATH,
    MODEL_LO_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    MODELS_DIR,
    RANDOM_STATE,
    REPORTS_DIR,
)
from krisha.features import (
    ALL_FEATURES,
    CAT_FEATURES,
    TARGET,
    build_features,
    clean,
    compute_ppsm_maps,
)

logger = logging.getLogger(__name__)


def load_dataset(db_path=DB_PATH) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("SELECT * FROM listings", conn)
    logger.info("Загружено %s объявлений", len(df))
    return df


def dedup_relistings(df: pd.DataFrame) -> pd.DataFrame:
    """Убирает перезалитые объявления: одна квартира под разными id.

    Отпечаток — район + комнаты + площадь + этаж/этажность + координаты
    (как krisha.db.listing_fingerprint). Оставляем самую свежую запись.
    """
    from krisha.db import listing_fingerprint

    def _fp(row: pd.Series) -> str | None:
        # NaN → None: listing_fingerprint ждёт «сырые» значения, а не pandas-NaN
        d = {k: (None if pd.isna(v) else v) for k, v in row.items()}
        return listing_fingerprint(d)

    fp = df.apply(_fp, axis=1)
    fp = fp.fillna(pd.Series((f"solo:{i}" for i in df.index), index=df.index))
    order_col = next(
        (c for c in ("last_seen", "scraped_at", "id") if c in df), None
    )
    before = len(df)
    if order_col is not None:
        df = df.sort_values(order_col, na_position="first")
    df = df.loc[~fp.reindex(df.index).duplicated(keep="last")]
    logger.info("Дедуп перезалитых: %d → %d строк", before, len(df))
    return df.reset_index(drop=True)


def building_groups(df: pd.DataFrame) -> pd.Series:
    """Группа «здание» для сплита: координаты с точностью ~10 м, иначе id."""
    def key(row) -> str:
        lat, lon = row.get("lat"), row.get("lon")
        if pd.notna(lat) and pd.notna(lon):
            return f"{float(lat):.4f}|{float(lon):.4f}"
        return f"id:{row.get('id', row.name)}"

    return df.apply(key, axis=1)


def baseline_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Baseline: медианная цена за м² по (район, число комнат) × площадь."""
    ppsm = train.assign(ppsm=train["price"] / train["area"])
    by_group = ppsm.groupby(["district", "rooms"])["ppsm"].median()
    by_district = ppsm.groupby("district")["ppsm"].median()
    global_median = ppsm["ppsm"].median()

    preds = []
    for _, row in test.iterrows():
        val = by_group.get((row["district"], row["rooms"]))
        if val is None or pd.isna(val):
            val = by_district.get(row["district"], global_median)
        preds.append(val * row["area"])
    return np.array(preds)


def evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "mape": float(mean_absolute_percentage_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
    }


# --- Доверительный интервал цены: квантильные модели + CQR ----------------
QUANTILE_ALPHA_LO = 0.10  # нижний квантиль log(price)
QUANTILE_ALPHA_HI = 0.90  # верхний квантиль log(price)
INTERVAL_TARGET_COVERAGE = 0.80  # целевая доля цен, попавших в интервал
QUANTILE_ITERATIONS = 800  # квантильным моделям хватает меньше — CQR чинит покрытие


def _fit_quantile(pool: Pool, alpha: float, iterations: int) -> CatBoostRegressor:
    model = CatBoostRegressor(
        iterations=iterations,
        learning_rate=0.05,
        depth=8,
        loss_function=f"Quantile:alpha={alpha}",
        random_seed=RANDOM_STATE,
        verbose=False,
    )
    model.fit(pool)
    return model


def train_quantile_interval(
    raw_train: pd.DataFrame,
    ppsm_maps: dict,
    spatial_ref: dict,
    test_df: pd.DataFrame,
    iterations: int = QUANTILE_ITERATIONS,
) -> tuple[CatBoostRegressor, CatBoostRegressor, dict]:
    """Квантильные модели q10/q90 + конформная калибровка интервала (CQR).

    Метод (Conformalized Quantile Regression, Romano et al. 2019):
    1. квантильные модели обучаем на fit-части train;
    2. на отложенной calib-части считаем conformity score
       E_i = max(lo_i - y_i, y_i - hi_i) в лог-шкале;
    3. сдвиг Q (квантиль E уровня ⌈(n+1)·c⌉/n) расширяет интервал так, чтобы
       покрытие было ≥ целевого с конечно-выборочной гарантией — независимо
       от того, насколько точны сами квантильные модели.

    fit/calib делим по «зданиям» (как основной сплит) — иначе квартиры одного
    дома по обе стороны делают conformity-скоры оптимистичными.
    Покрытие/ширину меряем на holdout test_df, он в калибровке не участвует.
    """
    groups = building_groups(raw_train)
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=RANDOM_STATE)
    fit_idx, cal_idx = next(splitter.split(raw_train, groups=groups))
    fit_raw = raw_train.iloc[fit_idx].reset_index(drop=True)
    cal_raw = raw_train.iloc[cal_idx].reset_index(drop=True)
    fit_df = build_features(fit_raw, ppsm_maps=ppsm_maps, spatial_ref=spatial_ref)
    cal_df = build_features(cal_raw, ppsm_maps=ppsm_maps, spatial_ref=spatial_ref)
    fit_pool = Pool(fit_df[ALL_FEATURES], fit_df[TARGET], cat_features=CAT_FEATURES)
    cal_pool = Pool(cal_df[ALL_FEATURES], cat_features=CAT_FEATURES)

    model_lo = _fit_quantile(fit_pool, QUANTILE_ALPHA_LO, iterations)
    model_hi = _fit_quantile(fit_pool, QUANTILE_ALPHA_HI, iterations)

    # CQR-сдвиг в лог-шкале на калибровочной части
    y_cal = cal_df[TARGET].to_numpy()
    lo_cal = model_lo.predict(cal_pool)
    hi_cal = model_hi.predict(cal_pool)
    scores = np.maximum(lo_cal - y_cal, y_cal - hi_cal)
    n = len(scores)
    level = min(1.0, np.ceil((n + 1) * INTERVAL_TARGET_COVERAGE) / n)
    offset = max(float(np.quantile(scores, level, method="higher")), 0.0)

    # Оценка покрытия и ширины на holdout (expm1 монотонна → сохраняет квантили)
    test_pool = Pool(test_df[ALL_FEATURES], cat_features=CAT_FEATURES)
    price_lo = np.expm1(model_lo.predict(test_pool) - offset)
    price_hi = np.expm1(model_hi.predict(test_pool) + offset)
    y_true = test_df["price"].to_numpy()
    covered = (y_true >= price_lo) & (y_true <= price_hi)
    mid = np.maximum((price_lo + price_hi) / 2.0, 1.0)
    width_pct = (price_hi - price_lo) / mid

    interval_meta = {
        "alpha_lo": QUANTILE_ALPHA_LO,
        "alpha_hi": QUANTILE_ALPHA_HI,
        "target_coverage": INTERVAL_TARGET_COVERAGE,
        "cqr_offset_log": offset,
        "coverage_test": float(np.mean(covered)),
        "median_width_pct": float(np.median(width_pct)),
        "n_fit": len(fit_df),
        "n_calib": len(cal_df),
    }
    logger.info("Интервал (CQR): %s", json.dumps(interval_meta))
    return model_lo, model_hi, interval_meta


def train(df: pd.DataFrame | None = None, iterations: int = 2000, save: bool = True) -> dict:
    """Полный пайплайн обучения. Возвращает метрики (model vs baseline)."""
    if df is None:
        df = load_dataset()
    df = clean(df)
    logger.info("После очистки: %s строк", len(df))
    # Зоны чиним до сплита: ppsm-статистика должна считаться по верным районам
    from krisha.zones import resolve_zones

    df = resolve_zones(df)
    # Честная схема валидации:
    # 1) дедуп перезалитых объявлений (одна квартира под разными id);
    # 2) сплит по «зданиям» (координаты), а не по строкам — иначе почти
    #    одинаковые квартиры из одного дома попадают и в train, и в test,
    #    и метрики выглядят лучше, чем работает модель на новых домах.
    df = dedup_relistings(df)
    groups = building_groups(df)
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=RANDOM_STATE)
    train_idx, test_idx = next(splitter.split(df, groups=groups))
    raw_train = df.iloc[train_idx].reset_index(drop=True)
    raw_test = df.iloc[test_idx].reset_index(drop=True)

    # ₸/м²-статистику считаем только на train, чтобы не было утечки в метрики
    ppsm_maps = compute_ppsm_maps(raw_train)
    from krisha.spatial import build_spatial_ref, self_indices_for

    spatial_ref = build_spatial_ref(raw_train)
    # На train сосед-«сам» исключается из KNN — иначе утечка таргета
    train_df = build_features(
        raw_train, ppsm_maps=ppsm_maps, spatial_ref=spatial_ref,
        knn_self_indices=self_indices_for(raw_train),
    )
    test_df = build_features(raw_test, ppsm_maps=ppsm_maps, spatial_ref=spatial_ref)
    train_pool = Pool(train_df[ALL_FEATURES], train_df[TARGET], cat_features=CAT_FEATURES)
    test_pool = Pool(test_df[ALL_FEATURES], test_df[TARGET], cat_features=CAT_FEATURES)

    model = CatBoostRegressor(
        iterations=iterations,
        learning_rate=0.05,
        depth=8,
        loss_function="RMSE",
        random_seed=RANDOM_STATE,
        early_stopping_rounds=100,
        verbose=200,
    )
    model.fit(train_pool, eval_set=test_pool)

    y_true = test_df["price"].to_numpy()
    y_model = np.expm1(model.predict(test_pool))
    y_base = baseline_predict(train_df, test_df)

    model_lo, model_hi, interval_meta = train_quantile_interval(
        raw_train, ppsm_maps, spatial_ref, test_df
    )

    metrics = {
        "model": evaluate(y_true, y_model),
        "baseline": evaluate(y_true, y_base),
        "interval": interval_meta,
        "n_train": len(train_df),
        "n_test": len(test_df),
        "split": "group_shuffle_by_building + dedup relistings",
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("Метрики: %s", json.dumps(metrics, indent=2))

    if save:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model.save_model(str(MODEL_PATH))
        model_lo.save_model(str(MODEL_LO_PATH))
        model_hi.save_model(str(MODEL_HI_PATH))
        from krisha.spatial import save_spatial_ref

        save_spatial_ref(spatial_ref)
        MODEL_META_PATH.write_text(json.dumps(
            {
                "features": ALL_FEATURES,
                "cat_features": CAT_FEATURES,
                "metrics": metrics,
                "ppsm_maps": ppsm_maps,
            },
            ensure_ascii=False, indent=2,
        ))
        _save_shap_report(model, test_df)
        # История метрик — тренд MAE/MAPE по переобучениям (мониторинг)
        try:
            from krisha.monitoring import append_metrics_history

            append_metrics_history(metrics)
        except Exception as exc:
            logger.warning("Не удалось записать историю метрик: %s", exc)
        # Снапшот статистики рынка — деплой без БД отдаёт /api/stats из него
        try:
            from krisha.stats import snapshot_stats
            snapshot_stats()
        except Exception as exc:
            logger.warning("Не удалось сохранить снапшот статистики: %s", exc)
        logger.info("Модель сохранена: %s", MODEL_PATH)
    return metrics


def _save_shap_report(model: CatBoostRegressor, test_df: pd.DataFrame) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import shap

        explainer = shap.TreeExplainer(model)
        sample = test_df[ALL_FEATURES].sample(min(500, len(test_df)), random_state=RANDOM_STATE)
        shap_values = explainer.shap_values(
            Pool(sample, cat_features=CAT_FEATURES)
        )
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        shap.summary_plot(shap_values, sample, show=False)
        plt.tight_layout()
        plt.savefig(REPORTS_DIR / "shap_summary.png", dpi=150)
        plt.close()
        logger.info("SHAP-отчёт: %s", REPORTS_DIR / "shap_summary.png")
    except Exception as exc:  # SHAP не должен ронять обучение
        logger.warning("Не удалось построить SHAP-отчёт: %s", exc)
