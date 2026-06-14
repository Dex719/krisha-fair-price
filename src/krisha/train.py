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
from sklearn.model_selection import train_test_split

from krisha.config import (
    DB_PATH,
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
    reconstruct_price,
    smearing_factor,
)

logger = logging.getLogger(__name__)


def load_dataset(db_path=DB_PATH) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("SELECT * FROM listings", conn)
    logger.info("Загружено %s объявлений", len(df))
    return df


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


def train(df: pd.DataFrame | None = None, iterations: int = 2000, save: bool = True) -> dict:
    """Полный пайплайн обучения. Возвращает метрики (model vs baseline)."""
    if df is None:
        df = load_dataset()
    df = clean(df)
    logger.info("После очистки: %s строк", len(df))

    raw_train, raw_test = train_test_split(df, test_size=0.2, random_state=RANDOM_STATE)
    # ₸/м²-статистику считаем только на train, чтобы не было утечки в метрики
    ppsm_maps = compute_ppsm_maps(raw_train)
    train_df = build_features(raw_train, ppsm_maps=ppsm_maps)
    test_df = build_features(raw_test, ppsm_maps=ppsm_maps)
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

    # Smearing-коррекция лог-смещения: считаем множитель на TRAIN-остатках,
    # чтобы не подсматривать в test (см. krisha.features.smearing_factor).
    train_log_pred = model.predict(train_pool)
    smearing = smearing_factor(train_df[TARGET].to_numpy(), train_log_pred)
    logger.info("Smearing-фактор (train): %.4f", smearing)

    # Метрики считаем на ВОССТАНОВЛЕННОЙ полной цене, а не на лог-таргете.
    y_true = test_df["price"].to_numpy()
    test_log_pred = model.predict(test_pool)
    y_model = reconstruct_price(test_log_pred, test_df["area"].to_numpy(), smearing)
    y_model_raw = reconstruct_price(test_log_pred, test_df["area"].to_numpy(), 1.0)
    y_base = baseline_predict(train_df, test_df)

    metrics = {
        "model": evaluate(y_true, y_model),
        "model_no_smearing": evaluate(y_true, y_model_raw),
        "baseline": evaluate(y_true, y_base),
        "smearing": smearing,
        "n_train": len(train_df),
        "n_test": len(test_df),
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("Метрики: %s", json.dumps(metrics, indent=2))

    if save:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model.save_model(str(MODEL_PATH))
        MODEL_META_PATH.write_text(json.dumps(
            {
                "features": ALL_FEATURES,
                "cat_features": CAT_FEATURES,
                "target": TARGET,
                "smearing": smearing,
                "metrics": metrics,
                "ppsm_maps": ppsm_maps,
            },
            ensure_ascii=False, indent=2,
        ))
        _save_shap_report(model, test_df)
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
