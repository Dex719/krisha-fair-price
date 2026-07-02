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

    metrics = {
        "model": evaluate(y_true, y_model),
        "baseline": evaluate(y_true, y_base),
        "n_train": len(train_df),
        "n_test": len(test_df),
        "split": "group_shuffle_by_building + dedup relistings",
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("Метрики: %s", json.dumps(metrics, indent=2))

    if save:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model.save_model(str(MODEL_PATH))
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
