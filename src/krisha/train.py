"""Обучение CatBoost на log(price) + сравнение с baseline + SHAP-отчёт.

Запуск: `python scripts/train.py`. Результат:
- models/model.cbm + models/model_meta.json (метрики, список фичей)
- reports/shap_summary.png
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
from sklearn.model_selection import GroupShuffleSplit

from krisha.config import (
    ALMATY_BBOX,
    DB_PATH,
    MODEL_GATE_SAMPLES_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    MODEL_QUANTILE_PATH,
    MODELS_DIR,
    RANDOM_STATE,
    REPORTS_DIR,
    STALE_DELISTED_DAYS,
)
from krisha.features import (
    ALL_FEATURES,
    CAT_FEATURES,
    TARGET,
    build_features,
    clean,
    compute_ppsm_maps,
)
from krisha.interval import MIN_INTERVAL_WIDTH_LOG, finalize_interval

logger = logging.getLogger(__name__)

# issue #104: доля датасета, отводимая под test при недостатке данных для
# фиксированного окна в TEST_WINDOW_DAYS (ранний этап проекта / БД маленькая).
TEST_WINDOW_DAYS = 14
TEST_MIN_FRACTION = 0.10


def _filter_stale_and_out_of_area(df: pd.DataFrame) -> pd.DataFrame:
    """issue #104: снятые месяцы назад лоты и чужие города не должны учить модель.

    - is_active=1 ИЛИ delisted_at не старше STALE_DELISTED_DAYS дней: лот, снятый
      давно, обучается на своей последней цене с first_seen из прошлого — цена
      успела устареть сильнее, чем «висел активным».
    - координаты вне bbox Алматы: чужой город в базе (битый парсинг/ручной ввод)
      портит ppsm_maps и hex-референсы. Лоты без координат не трогаем — их чинит
      resolve_zones/district дальше по пайплайну.
    """
    before = len(df)
    if {"is_active", "delisted_at"}.issubset(df.columns):
        delisted_at = pd.to_datetime(df["delisted_at"], errors="coerce", utc=True)
        cutoff = pd.Timestamp.now(tz="utc") - pd.Timedelta(days=STALE_DELISTED_DAYS)
        recently_delisted = delisted_at >= cutoff
        is_active = pd.to_numeric(df["is_active"], errors="coerce").fillna(1) == 1
        df = df[is_active | recently_delisted]
    if {"lat", "lon"}.issubset(df.columns):
        lat = pd.to_numeric(df["lat"], errors="coerce")
        lon = pd.to_numeric(df["lon"], errors="coerce")
        in_bbox = lat.between(ALMATY_BBOX["lat_min"], ALMATY_BBOX["lat_max"]) & lon.between(
            ALMATY_BBOX["lon_min"], ALMATY_BBOX["lon_max"]
        )
        no_coords = lat.isna() | lon.isna()
        df = df[in_bbox | no_coords]
    dropped = before - len(df)
    if dropped:
        logger.info(
            "issue #104: отфильтровано %s устаревших/иногородних объявлений из train", dropped
        )
    return df.reset_index(drop=True)


def load_dataset(db_path=DB_PATH) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("SELECT * FROM listings", conn)
    # issue #117 (доп. по итогам аудитов): predict_from_url делает upsert с
    # любым id с krisha.kz — включая другие города, без валидации города/типа
    # сделки. Один пользователь, проверяющий квартиры в Астане, портит
    # ppsm_maps и хвосты распределений следующего ретрейна. source="user"
    # полностью исключаем из train-выборки.
    if "source" in df.columns:
        before = len(df)
        df = df[df["source"] != "user"].reset_index(drop=True)
        dropped = before - len(df)
        if dropped:
            logger.info("Исключено %s пользовательских предиктов (issue #117) из train", dropped)
    df = _filter_stale_and_out_of_area(df)
    logger.info("Загружено %s объявлений", len(df))
    return df


def _fingerprints(df: pd.DataFrame) -> pd.Series:
    """Отпечаток каждой строки (krisha.db.listing_fingerprint), None → уникальный."""
    from krisha.db import listing_fingerprint

    def _fp(row: pd.Series) -> str | None:
        # NaN → None: listing_fingerprint ждёт «сырые» значения, а не pandas-NaN
        d = {k: (None if pd.isna(v) else v) for k, v in row.items()}
        return listing_fingerprint(d)

    fp = df.apply(_fp, axis=1)
    return fp.fillna(pd.Series((f"solo:{i}" for i in df.index), index=df.index))


DEDUP_PRICE_TOLERANCE = 0.02  # issue #129: fingerprint-совпадение — дубль только при близкой цене


def _cluster_by_price(prices: pd.Series, tolerance: float = DEDUP_PRICE_TOLERANCE) -> dict:
    """Кластеризует цены одной fingerprint-группы по близости (цепочкой).

    Сортирует цены по возрастанию и режет на новый кластер там, где сосед
    отличается больше чем на `tolerance` (по умолчанию ~2%). Возвращает
    {index_label: cluster_id}. Это цепочная (chain-linkage) кластеризация:
    внутри кластера соседние цены близки, но крайние могут разойтись
    больше tolerance — приемлемо для простой эвристики «перезалив почти
    не меняет цену».
    """
    order = prices.sort_values().index
    clusters: dict = {}
    cluster_id = -1
    prev = None
    for i in order:
        p = prices[i]
        if prev is None or prev == 0 or abs(p - prev) > tolerance * abs(prev):
            cluster_id += 1
        clusters[i] = cluster_id
        prev = p
    return clusters


def dedup_relistings(df: pd.DataFrame) -> pd.DataFrame:
    """Убирает перезалитые объявления: одна квартира под разными id.

    Отпечаток — район + комнаты + площадь + этаж/этажность + координаты
    (как krisha.db.listing_fingerprint). Оставляем самую свежую запись.

    issue #129: в новостройках пин часто ставится на ЖК, а не на конкретный
    дом/квартиру — десятки реально разных квартир одной планировки на одном
    этаже соседних блоков получают тот же fingerprint и раньше выкидывались
    целиком как «дубли» (в train доходила лишь половина базы). Теперь
    совпадение fingerprint считаем дублем, только если у строк группы ещё и
    цена совпадает в пределах ~`DEDUP_PRICE_TOLERANCE`: реальный перезалив
    почти не меняет цену, а разные квартиры одной планировки в новостройке —
    почти всегда меняют. Строки без цены внутри группы, где у остальных
    цена есть, перестраховываемся консервативно — считаем дублем (как
    раньше), т.к. проверить их нечем.

    Диагностика (распределение размеров fingerprint-групп и доля таких
    строк в новостройках) кладётся в `df.attrs["dedup_stats"]` для отчёта
    обучения.
    """
    fp = _fingerprints(df)
    order_col = next(
        (c for c in ("last_seen", "scraped_at", "id") if c in df), None
    )
    before = len(df)
    has_price = "price" in df.columns

    # --- диагностика: распределение размеров fingerprint-групп (issue #129) ---
    group_sizes = fp.value_counts()
    dup_groups = group_sizes[
        (group_sizes > 1) & (~group_sizes.index.astype(str).str.startswith("solo:"))
    ]
    size_hist: dict[str, int] = {}
    for size in dup_groups.to_numpy():
        bucket = "2" if size == 2 else "3-5" if size <= 5 else "6-10" if size <= 10 else "11+"
        size_hist[bucket] = size_hist.get(bucket, 0) + 1
    rows_in_dup_groups = fp.isin(dup_groups.index)
    new_building_share = None
    if "complex_name" in df.columns and rows_in_dup_groups.any():
        new_building_share = round(
            float(df.loc[rows_in_dup_groups, "complex_name"].notna().mean()), 3
        )

    if order_col is not None:
        df = df.sort_values(order_col, na_position="first")
    fp = fp.reindex(df.index)
    # позиция строки в уже отсортированном df — больше = свежее
    pos = pd.Series(range(len(df)), index=df.index)

    if has_price:
        keep = pd.Series(True, index=df.index)
        for _, members in fp.groupby(fp).groups.items():
            if len(members) < 2:
                continue
            prices = df.loc[members, "price"]
            priced = prices[prices.notna()]
            keep.loc[members] = False
            if len(priced) >= 2:
                clusters = _cluster_by_price(priced)
                for cid in set(clusters.values()):
                    cluster_members = [i for i, c in clusters.items() if c == cid]
                    keep.loc[max(cluster_members, key=lambda i: pos[i])] = True
                # без цены внутри группы, где у остальных цена есть —
                # консервативно считаем дублем (уже False выше)
            else:
                # цены почти нет — нечем разбивать по цене, старое поведение
                keep.loc[max(members, key=lambda i: pos[i])] = True
        df = df.loc[keep]
    else:
        df = df.loc[~fp.duplicated(keep="last")]

    dropped = before - len(df)
    logger.info(
        "Дедуп перезалитых (issue #129, price tolerance %.0f%%): %d → %d строк",
        DEDUP_PRICE_TOLERANCE * 100, before, len(df),
    )
    out = df.reset_index(drop=True)
    out.attrs["dedup_stats"] = {
        "rows_before": before,
        "rows_after": len(out),
        "dropped": dropped,
        "dropped_pct": round(100 * dropped / before, 2) if before else 0.0,
        "fingerprint_dup_groups": int(len(dup_groups)),
        "fingerprint_group_size_histogram": size_hist,
        "new_building_share_of_dup_groups": new_building_share,
    }
    return out


def building_groups(df: pd.DataFrame) -> pd.Series:
    """Группа «здание» для сплита: координаты с точностью ~10 м, иначе id."""
    def key(row) -> str:
        lat, lon = row.get("lat"), row.get("lon")
        if pd.notna(lat) and pd.notna(lon):
            return f"{float(lat):.4f}|{float(lon):.4f}"
        return f"id:{row.get('id', row.name)}"

    return df.apply(key, axis=1)


def time_based_split(
    df: pd.DataFrame,
    window_days: int = TEST_WINDOW_DAYS,
    min_fraction: float = TEST_MIN_FRACTION,
) -> tuple[np.ndarray, np.ndarray]:
    """issue #104: test = самые свежие объявления по first_seen, а не случайный сплит.

    Случайный (пусть и по зданиям) сплит смешивает train и test по времени —
    объявления с обеих сторон видели один и тот же ценовой уровень, метрики
    оптимистичны, в проде модель на самом деле экстраполирует в будущее.

    Окно — последние window_days дней по first_seen; если это меньше
    min_fraction от датасета (мало истории / БД маленькая), берём последние
    min_fraction по времени вместо фиксированного окна.
    Нет first_seen вообще (старая БД/тесты) — весь датасет в train, test
    пустой, вызывающий код (train() ниже) на это рассчитывает.
    """
    if "first_seen" not in df.columns:
        return df.index.to_numpy(), np.array([], dtype=int)

    ts = pd.to_datetime(df["first_seen"], errors="coerce", utc=True)
    if ts.notna().sum() == 0:
        return df.index.to_numpy(), np.array([], dtype=int)

    order = ts.rank(method="first")  # стабильный порядок и для NaT (в конец)
    max_ts = ts.max()
    cutoff = max_ts - pd.Timedelta(days=window_days)
    test_mask = (ts >= cutoff).fillna(False)

    min_test_n = max(1, int(min_fraction * ts.notna().sum()))
    if test_mask.sum() < min_test_n:
        thresh = order.quantile(1 - min_fraction)
        test_mask = order > thresh

    train_idx = df.index[~test_mask].to_numpy()
    test_idx = df.index[test_mask].to_numpy()
    return train_idx, test_idx


def purge_leaked_train_rows(
    raw_train: pd.DataFrame, raw_test: pd.DataFrame
) -> tuple[pd.DataFrame, int]:
    """issue #104 (доп.): чистит train от строк, «протекающих» из test.

    Временной сплит режет по дате, но объявление могло быть перевыставлено
    (тот же fingerprint) — тогда почти идентичная квартира лежит и в train,
    и в «будущем» test, и метрики снова оптимистичны. Убираем из train любую
    строку с fingerprint, встречающимся в test (test как «будущее» не трогаем).

    Осознанно НЕ чистим по building-группе (issue #104 доработка после
    ревью): другие квартиры того же дома в train — легитимная информация,
    которая у модели в проде есть всегда (предикт нового лота в знакомом
    доме — основной кейс, не утечка). Purge по зданиям делал test
    искусственно сложнее прода и выкидывал из train лишние строки.
    """
    if len(raw_test) == 0:
        return raw_train, 0
    test_fp = set(_fingerprints(raw_test))
    train_fp = _fingerprints(raw_train)
    leak_mask = train_fp.isin(test_fp)
    purged = raw_train.loc[~leak_mask].reset_index(drop=True)
    n_purged = int(leak_mask.sum())
    if n_purged:
        logger.info("issue #104: purge %d строк train, протекающих в test (fingerprint)", n_purged)
    return purged, n_purged


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


def _save_gate_samples(
    y_true: np.ndarray, y_new: np.ndarray, y_old: np.ndarray,
    path: Path | str = MODEL_GATE_SAMPLES_PATH,
) -> None:
    """issue #106: пары APE (новая/старая модель) по каждой test-строке.

    Нужны model_gate.py для парного бутстрепа разницы MAPE — точнее плоского
    допуска ±0.5 п.п., который на ~1400 строках теста и пропускает деградации,
    и блокирует реальные улучшения (шум ~1.5-2σ на одном сплите).
    """
    ape_new = (np.abs(y_new - y_true) / np.maximum(y_true, 1.0)).tolist()
    ape_old = (np.abs(y_old - y_true) / np.maximum(y_true, 1.0)).tolist()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps({"ape_new": ape_new, "ape_old": ape_old}, separators=(",", ":"))
    )


# --- Доверительный интервал цены: MultiQuantile-модель + CQR --------------
QUANTILE_ALPHA_LO = 0.10  # нижний квантиль log(price)
QUANTILE_ALPHA_HI = 0.90  # верхний квантиль log(price)
INTERVAL_TARGET_COVERAGE = 0.80  # целевая доля цен, попавших в интервал
QUANTILE_ITERATIONS = 800  # верхний потолок итераций — реально режется early stopping (issue #132)
QUANTILE_EARLY_STOPPING_ROUNDS = 100  # как у точечной RMSE-модели ниже
CQR_SCALE_MAX = 10.0  # защита от переполнения expm1 при аномальном scale (см. MIN_INTERVAL_WIDTH_LOG)


def _fit_multiquantile(
    pool: Pool, iterations: int, eval_pool: Pool | None = None
) -> CatBoostRegressor:
    """Одна модель на оба квантиля разом (issue #132) вместо двух отдельных
    Quantile-моделей (model_lo/model_hi): `MultiQuantile:alpha=lo,hi` растит
    общие деревья на обе целевые квантили — они не могут пересечься (в
    отличие от двух независимо обученных моделей), и обучение почти вдвое
    быстрее двух отдельных `fit()`.

    `model.predict(pool)` возвращает массив `(n, 2)`: столбец 0 — квантиль
    QUANTILE_ALPHA_LO, столбец 1 — QUANTILE_ALPHA_HI (порядок совпадает с
    alpha-списком в loss_function).

    `eval_pool`, если задан, включает early stopping (issue #132: раньше
    квантильная модель училась вслепую фиксированные QUANTILE_ITERATIONS
    итераций без eval-set).
    """
    model = CatBoostRegressor(
        iterations=iterations,
        learning_rate=0.05,
        depth=8,
        loss_function=f"MultiQuantile:alpha={QUANTILE_ALPHA_LO},{QUANTILE_ALPHA_HI}",
        random_seed=RANDOM_STATE,
        early_stopping_rounds=QUANTILE_EARLY_STOPPING_ROUNDS if eval_pool is not None else None,
        verbose=False,
    )
    model.fit(pool, eval_set=eval_pool)
    return model


def train_quantile_interval(
    raw_train: pd.DataFrame,
    test_df: pd.DataFrame,
    point_pred: np.ndarray,
    iterations: int = QUANTILE_ITERATIONS,
) -> tuple[CatBoostRegressor, dict]:
    """MultiQuantile-модель q10+q90 (issue #132) + конформная калибровка (CQR).

    Метод (Conformalized Quantile Regression, Romano et al. 2019; вариант с
    нормировкой ширины — adaptive/normalized CQR):
    1. одна MultiQuantile-модель обучается на fit-части train (issue #132:
       раньше — две независимые Quantile-модели model_lo/model_hi; общее
       дерево гарантирует lo<=hi и обучается быстрее);
    2. на отложенной calib-части считаем conformity score, нормированный на
       ширину предсказанного интервала: E_i = max(lo_i - y_i, y_i - hi_i) / (hi_i - lo_i) —
       точки с изначально широким интервалом не наказываются сильнее узких.
       Сегментацию (Mondrian по району/типу) сознательно не делаем: на
       текущем объёме calib (~тысяча строк) — лотерея, добавлять только по
       результатам walk-forward стенда (issue #130);
    3. масштаб Q (квантиль E уровня ⌈(n+1)·c⌉/n) расширяет [lo, hi] на Q·(hi-lo)
       с каждой стороны так, чтобы покрытие было ≥ целевого с конечно-
       выборочной гарантией — независимо от точности квантильной модели.

    issue #105: fit/calib раньше делились случайно (GroupShuffleSplit), и
    ppsm_maps/spatial_ref для ОБОИХ строились на полном raw_train, т.е. цены
    calib-строк были вшиты в их же district_ppsm/hex_ppsm/knn — conformity-
    скоры были занижены, реальное покрытие ниже заявленного (coverage_test
    0.789 при цели 0.80). Фикс:
    - calib = последние 2 недели train по времени (не случайный кусок) —
      согласовано с общим временным сплитом (issue #104);
    - ppsm_maps/spatial_ref для fit и calib строятся ТОЛЬКО по fit-части —
      calib в этот референс не входит вообще, так что self-leak в KNN для
      calib-строк снимается автоматически (не нужно даже передавать
      knn_self_indices для cal_df — их там просто нет).

    issue #132: квантильная модель раньше училась вслепую фиксированные
    QUANTILE_ITERATIONS итераций без eval-set. Добавлен ещё один временной
    под-сплит — внутри fit-части выделяется её же самый свежий кусок как
    val (та же time_based_split-логика, что и общий train/test и fit/calib,
    согласованно продолжает её «назад по времени»): probe-модель находит
    best_iterations через early stopping, финальная MultiQuantile-модель
    переобучается на ВСЕЙ fit-части с этим числом итераций — тот же паттерн,
    что и у точечной RMSE-модели в train() (probe → best_iterations → финал
    на полном train).

    Покрытие/ширину меряем на holdout test_df через общий finalize_interval()
    (issue #105 доп.: раньше predict.py свопал/растягивал границы интервала,
    а train.py при подсчёте coverage_test — нет, метрики не совпадали с тем,
    что реально видит пользователь).
    """
    fit_idx, cal_idx = time_based_split(raw_train, window_days=TEST_WINDOW_DAYS)
    if len(cal_idx) == 0:  # нет first_seen (тесты/старая БД) — фолбэк на группы
        groups = building_groups(raw_train)
        splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=RANDOM_STATE)
        fit_idx, cal_idx = next(splitter.split(raw_train, groups=groups))
    fit_raw = raw_train.iloc[fit_idx].reset_index(drop=True)
    cal_raw = raw_train.iloc[cal_idx].reset_index(drop=True)

    from krisha.spatial import build_spatial_ref, self_indices_for

    ppsm_maps_fit = compute_ppsm_maps(fit_raw)
    spatial_ref_fit = build_spatial_ref(fit_raw)
    fit_df = build_features(
        fit_raw, ppsm_maps=ppsm_maps_fit, spatial_ref=spatial_ref_fit,
        knn_self_indices=self_indices_for(fit_raw),
    )
    cal_df = build_features(cal_raw, ppsm_maps=ppsm_maps_fit, spatial_ref=spatial_ref_fit)
    fit_pool = Pool(fit_df[ALL_FEATURES], fit_df[TARGET], cat_features=CAT_FEATURES)
    cal_pool = Pool(cal_df[ALL_FEATURES], cat_features=CAT_FEATURES)

    # issue #132: early stopping для квантильной модели — временной
    # под-сплит внутри fit-части (самый свежий кусок fit = val), а не
    # групповой по зданиям: должен быть согласован с уже отрезанным по
    # времени calib-окном, а не мешать train/val по времени случайно.
    es_fit_idx, es_val_idx = time_based_split(fit_raw, window_days=TEST_WINDOW_DAYS)
    if len(es_val_idx) == 0:
        groups_es = building_groups(fit_raw)
        splitter_es = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=RANDOM_STATE)
        es_fit_idx, es_val_idx = next(splitter_es.split(fit_raw, groups=groups_es))
    es_fit_raw = fit_raw.iloc[es_fit_idx].reset_index(drop=True)
    es_val_raw = fit_raw.iloc[es_val_idx].reset_index(drop=True)
    ppsm_maps_es = compute_ppsm_maps(es_fit_raw)
    spatial_ref_es = build_spatial_ref(es_fit_raw)
    es_fit_df = build_features(
        es_fit_raw, ppsm_maps=ppsm_maps_es, spatial_ref=spatial_ref_es,
        knn_self_indices=self_indices_for(es_fit_raw),
    )
    es_val_df = build_features(es_val_raw, ppsm_maps=ppsm_maps_es, spatial_ref=spatial_ref_es)
    es_fit_pool = Pool(es_fit_df[ALL_FEATURES], es_fit_df[TARGET], cat_features=CAT_FEATURES)
    es_val_pool = Pool(es_val_df[ALL_FEATURES], es_val_df[TARGET], cat_features=CAT_FEATURES)

    probe = _fit_multiquantile(es_fit_pool, iterations, eval_pool=es_val_pool)
    best_iterations = max(int(probe.tree_count_), 1)
    logger.info("Квантильная модель: early stopping по val — %s деревьев", best_iterations)

    model = _fit_multiquantile(fit_pool, best_iterations)

    # Conformity score на calib, нормированный на ширину предсказанного интервала
    y_cal = cal_df[TARGET].to_numpy()
    preds_cal = model.predict(cal_pool)
    lo_cal, hi_cal = preds_cal[:, 0], preds_cal[:, 1]
    width_cal = np.maximum(hi_cal - lo_cal, MIN_INTERVAL_WIDTH_LOG)
    scores = np.maximum(lo_cal - y_cal, y_cal - hi_cal) / width_cal
    n = len(scores)
    level = min(1.0, np.ceil((n + 1) * INTERVAL_TARGET_COVERAGE) / n)
    scale = min(max(float(np.quantile(scores, level, method="higher")), 0.0), CQR_SCALE_MAX)

    # Оценка покрытия и ширины на holdout (expm1 монотонна → сохраняет квантили)
    test_pool = Pool(test_df[ALL_FEATURES], cat_features=CAT_FEATURES)
    preds_test = model.predict(test_pool)
    lo_test, hi_test = preds_test[:, 0], preds_test[:, 1]
    width_test = np.maximum(hi_test - lo_test, MIN_INTERVAL_WIDTH_LOG)
    # клип лог-границ до expm1: даже с CQR_SCALE_MAX очень широкий
    # предсказанный интервал не должен переполнять float (overflow → NaN
    # ниже по цепочке при вычислении width_pct).
    log_lo = np.clip(lo_test - scale * width_test, -30.0, 30.0)
    log_hi = np.clip(hi_test + scale * width_test, -30.0, 30.0)
    price_lo = np.expm1(log_lo)
    price_hi = np.expm1(log_hi)
    price_lo, price_hi = finalize_interval(point_pred, price_lo, price_hi)
    y_true = test_df["price"].to_numpy()
    covered = (y_true >= price_lo) & (y_true <= price_hi)
    mid = np.maximum((price_lo + price_hi) / 2.0, 1.0)
    width_pct = (price_hi - price_lo) / mid

    interval_meta = {
        "alpha_lo": QUANTILE_ALPHA_LO,
        "alpha_hi": QUANTILE_ALPHA_HI,
        "target_coverage": INTERVAL_TARGET_COVERAGE,
        "cqr_scale": scale,  # множитель ширины интервала (нормированный CQR)
        "coverage_test": float(np.mean(covered)),
        "median_width_pct": float(np.median(width_pct)),
        "n_fit": len(fit_df),
        "n_calib": len(cal_df),
        "quantile_best_iterations": best_iterations,  # issue #132: early stopping
    }
    logger.info("Интервал (CQR): %s", json.dumps(interval_meta))
    return model, interval_meta


def train(
    df: pd.DataFrame | None = None,
    iterations: int = 2000,
    save: bool = True,
    old_model_path: str | Path | None = None,
) -> dict:
    """Полный пайплайн обучения. Возвращает метрики (model vs baseline).

    old_model_path — путь к прошлой model.cbm: если задан, старая модель
    оценивается на том же свежем test-сплите → metrics["old_model"], и
    метрический гейт сравнивает яблоки с яблоками (см. scripts/model_gate.py).
    """
    if df is None:
        df = load_dataset()
    df = clean(df)
    logger.info("После очистки: %s строк", len(df))
    # Зоны чиним до сплита: ppsm-статистика должна считаться по верным районам
    from krisha.zones import resolve_zones

    df = resolve_zones(df)
    # Честная схема валидации (issue #104):
    # 1) дедуп перезалитых объявлений (одна квартира под разными id);
    # 2) временной holdout — test — самые свежие объявления по first_seen, train —
    #    всё раньше (случайный сплит смешивал train/test по времени, метрики
    #    были оптимистичны, в проде модель на самом деле экстраполирует вперёд);
    # 3) purge — из train убираем строки, протекающие в test через fingerprint
    #    (перевыставление) или ту же «группу-здание» (issue #104 доп.).
    df = dedup_relistings(df)
    dedup_stats = df.attrs.get("dedup_stats", {})
    if dedup_stats:
        logger.info(
            "issue #129: выброшено дедупом %d строк (%.2f%%) — fingerprint-групп: %d, "
            "доля новостроек в них: %s",
            dedup_stats.get("dropped", 0),
            dedup_stats.get("dropped_pct", 0.0),
            dedup_stats.get("fingerprint_dup_groups", 0),
            dedup_stats.get("new_building_share_of_dup_groups"),
        )
    train_idx, test_idx = time_based_split(df)
    if len(test_idx) == 0:
        # Нет first_seen вообще (старая БД / ручной DataFrame без него) —
        # временной сплит невозможен, фолбэк на прежний случайный по зданиям.
        logger.warning("first_seen недоступен — фолбэк на group_shuffle-сплит по зданиям")
        groups = building_groups(df)
        splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=RANDOM_STATE)
        train_idx, test_idx = next(splitter.split(df, groups=groups))
    raw_train_all = df.iloc[train_idx].reset_index(drop=True)
    raw_test = df.iloc[test_idx].reset_index(drop=True)
    raw_train, n_purged = purge_leaked_train_rows(raw_train_all, raw_test)

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

    # Early stopping — по val-сплиту из train (по «зданиям»), а не по test:
    # иначе число деревьев подгоняется под тестовую выборку и метрики чуть
    # оптимистичнее реальности. Схема: probe-модель находит оптимальное число
    # итераций на val, финальная переобучается на всём train.
    #
    # issue #104 (доп.): ppsm_maps/spatial_ref для fit/val СВОИ, построенные
    # только на fit-части — раньше probe.fit использовал train_df, чьи
    # district_ppsm/hex_ppsm/knn считались по ВСЕМУ raw_train, включая val —
    # val-строки видели собственную цену в своих же референсных статистиках,
    # best_iterations выбирался по оценке, которая уже частично «списала».
    es_splitter = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=RANDOM_STATE)
    fit_idx, val_idx = next(es_splitter.split(raw_train, groups=building_groups(raw_train)))
    fit_raw_es = raw_train.iloc[fit_idx].reset_index(drop=True)
    val_raw_es = raw_train.iloc[val_idx].reset_index(drop=True)
    ppsm_maps_es = compute_ppsm_maps(fit_raw_es)
    spatial_ref_es = build_spatial_ref(fit_raw_es)
    fit_df_es = build_features(
        fit_raw_es, ppsm_maps=ppsm_maps_es, spatial_ref=spatial_ref_es,
        knn_self_indices=self_indices_for(fit_raw_es),
    )
    val_df_es = build_features(val_raw_es, ppsm_maps=ppsm_maps_es, spatial_ref=spatial_ref_es)
    fit_pool = Pool(
        fit_df_es[ALL_FEATURES], fit_df_es[TARGET], cat_features=CAT_FEATURES,
    )
    val_pool = Pool(
        val_df_es[ALL_FEATURES], val_df_es[TARGET], cat_features=CAT_FEATURES,
    )
    probe = CatBoostRegressor(
        iterations=iterations,
        learning_rate=0.05,
        depth=8,
        loss_function="RMSE",
        random_seed=RANDOM_STATE,
        early_stopping_rounds=100,
        verbose=200,
    )
    probe.fit(fit_pool, eval_set=val_pool)
    best_iterations = max(int(probe.tree_count_), 1)
    logger.info("Early stopping по val: %s деревьев", best_iterations)

    model = CatBoostRegressor(
        iterations=best_iterations,
        learning_rate=0.05,
        depth=8,
        loss_function="RMSE",
        random_seed=RANDOM_STATE,
        verbose=200,
    )
    model.fit(train_pool)

    y_true = test_df["price"].to_numpy()
    y_model = np.expm1(model.predict(test_pool))
    y_base = baseline_predict(train_df, test_df)

    quantile_model, interval_meta = train_quantile_interval(
        raw_train, test_df, point_pred=y_model
    )

    metrics = {
        "model": evaluate(y_true, y_model),
        "baseline": evaluate(y_true, y_base),
        "interval": interval_meta,
        "n_train": len(train_df),
        "n_test": len(test_df),
        "n_purged": n_purged,
        "dedup": dedup_stats,
        "best_iterations": best_iterations,
        "split": f"time_based (test = first_seen >= last {TEST_WINDOW_DAYS}d) + purge + dedup relistings",
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }

    # Прошлая модель на этом же test-сплите — честная база для гейта:
    # сравнение по метрикам из старого meta страдает от дрейфа данных
    # (test-выборки разных недель не совпадают).
    #
    # issue #106: если сравнение не удалось (feature-set поменялся и т.п.),
    # явно помечаем ошибку в meta вместо тихого пропуска — гейт (model_gate.py)
    # блокирует публикацию в этом случае, а не молча падает в слабый fallback.
    if old_model_path and Path(old_model_path).exists():
        try:
            old_model = CatBoostRegressor()
            old_model.load_model(str(old_model_path))
            y_old = np.expm1(old_model.predict(test_pool))
            metrics["old_model"] = evaluate(y_true, y_old)
            logger.info("Старая модель на новом test: %s", json.dumps(metrics["old_model"]))
            if save:
                _save_gate_samples(y_true, y_model, y_old)
        except Exception as exc:  # набор фичей мог измениться — гейт уходит в fail-closed
            metrics["old_model_error"] = str(exc)
            logger.warning("Не удалось оценить старую модель (%s): %s", old_model_path, exc)
    logger.info("Метрики: %s", json.dumps(metrics, indent=2))

    if save:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model.save_model(str(MODEL_PATH))
        quantile_model.save_model(str(MODEL_QUANTILE_PATH))
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
