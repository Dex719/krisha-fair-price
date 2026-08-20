#!/usr/bin/env python
"""Честное сравнение всех исторических моделей на ОДНОМ свежем test-сплите.

Методика — расширение метрического гейта (scripts/model_gate.py, train.py
--compare-old) со «старая vs новая» на все эпохи из git-истории: гейт
оценивает прошлую модель на test_pool текущего ретрейна, здесь тот же
test_pool скармливается N чекпойнтам.

Воспроизведение фрейма эпохи-якоря (последний retrain-коммит из ERAS,
его мета лежит в models/model_meta.json):
- датасет из SQLite «как при обучении»: те же фильтры, что load_dataset
  (source != "user", stale-фильтр, bbox), но с «сейчас», замороженным на
  metrics.trained_at из меты, плюс отсечка first_seen <= trained_at —
  строки, дописанные в базу после обучения, в фрейм не попадают;
- дальше боевой пайплайн из krisha.train/krisha.features: clean →
  resolve_zones → dedup_relistings → time_based_split → purge;
- фичи ЕДИНЫЕ для всех эпох — по картам train-среза эпохи-якоря (ppsm_maps из
  models/model_meta.json, spatial_ref из models/spatial_ref.json), как
  гейт кормит старую модель картами НОВОГО train-среза. Старые эпохи
  получают чуть сдвинутые ковариаты, но сдвиг одинаков для всех — любая
  разница метрик изолирована до весов модели.

Валидация по якорям обязательна: последняя эпоха ERAS (metrics.model),
предпоследняя (metrics.old_model — посчитана гейтом ровно на этом тесте)
и baseline из меты должны сойтись с воспроизведёнными значениями; иначе
фрейм собран не так — exit 1.

Запуск:
    python scripts/compare_models.py --eras-dir <dir с <commit>/model.cbm>
Результат — reports/model_comparison.json + markdown-таблица в stdout.
"""

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool

from krisha.config import (
    ALMATY_BBOX,
    DB_PATH,
    MODEL_META_PATH,
    REPORTS_DIR,
    SPATIAL_REF_PATH,
    STALE_DELISTED_DAYS,
)
from krisha.features import CAT_FEATURES, build_features
from krisha.features import clean as clean_listings
from krisha.spatial import self_indices_for
from krisha.train import (
    baseline_predict,
    dedup_relistings,
    evaluate,
    purge_leaked_train_rows,
    time_based_split,
)
from krisha.zones import resolve_zones

logger = logging.getLogger(__name__)

# Допуск на дрейф якорей: база мутабельна (upsert цен после ретрейна),
# поэтому побитового совпадения не ждём, но > 0.4 п.п. MAPE — признак
# неверно собранного фрейма, а не дрейфа данных.
ANCHOR_TOLERANCE = 0.004

# Реестр эпох из git-истории models/model.cbm (git log --follow).
# target_transform у всех log1p (TARGET='log_price' всю историю проекта),
# но храним явно: эпоха с другим таргетом должна быть исключена, а не
# молча инвертирована не той функцией.
ERAS: list[dict[str, str]] = [
    {"commit": "fd06da4", "date": "2026-06-12",
     "label": "Первая модель: CatBoost на 6075 объявлениях"},
    {"commit": "29cf732", "date": "2026-06-12",
     "label": "Ретрейн на 7050 объявлениях (те же 23 фичи)"},
    {"commit": "b160ff7", "date": "2026-06-12",
     "label": "+raw_params: ремонт, санузел, мебель, парковка, охрана"},
    {"commit": "54a3752", "date": "2026-06-12",
     "label": "+справочник ЖК: застройщик, класс, год сдачи"},
    {"commit": "b26a657", "date": "2026-06-12",
     "label": "+OSM POI: метро/школы/парки, walk_score"},
    {"commit": "c32ed2b", "date": "2026-07-02",
     "label": "+H3-гексагоны; честная CV (dedup + group split), v0.2.0"},
    {"commit": "e7c837e", "date": "2026-08-02",
     "label": "Weekly retrain 02.08: time-based holdout + purge"},
    {"commit": "c244821", "date": "2026-08-09",
     "label": "Weekly retrain 09.08"},
    {"commit": "974c893", "date": "2026-08-16",
     "label": "Weekly retrain 16.08 (текущий прод)"},
]
KNOWN_TRANSFORMS = {"log1p"}  # log1p → инверсия np.expm1

# Чекпойнты истории models/model.cbm, сознательно НЕ вошедшие в ERAS —
# исключение документируется в отчёте, а не замалчивается.
EXCLUDED_CHECKPOINTS: list[dict[str, str]] = [
    {"commit": "700ba4b", "date": "2026-07-02",
     "reason": "тот же обученный артефакт, что c32ed2b (own MAPE в мете совпадает "
               "бит-в-бит, тот же день и seed): коммит добавил CQR-интервал, "
               "основную модель не менял — отдельной эпохи нет"},
]


def load_dataset_asof(db_path: Path | str, trained_at: pd.Timestamp) -> pd.DataFrame:
    """Датасет «как видел его train() в момент trained_at».

    Реплика krisha.train.load_dataset с двумя отличиями, из-за которых её
    нельзя позвать напрямую:
    - stale-фильтр _filter_stale_and_out_of_area берёт pd.Timestamp.now(),
      а для воспроизведения сплита «сейчас» заморожено на trained_at;
    - добавлена as-of отсечка first_seen <= trained_at: строки, дописанные
      в базу после обучения (кроны сбора работают ежедневно), не должны
      менять состав теста. Строки без first_seen остаются — как в train().
    Пороговые константы (STALE_DELISTED_DAYS, ALMATY_BBOX) — боевые.
    """
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("SELECT * FROM listings", conn)
    if "source" in df.columns:
        df = df[df["source"] != "user"]  # issue #117: пользовательские предикты
    if {"is_active", "delisted_at"}.issubset(df.columns):
        delisted_at = pd.to_datetime(df["delisted_at"], errors="coerce", utc=True)
        cutoff = trained_at - pd.Timedelta(days=STALE_DELISTED_DAYS)
        is_active = pd.to_numeric(df["is_active"], errors="coerce").fillna(1) == 1
        df = df[is_active | (delisted_at >= cutoff)]
    if {"lat", "lon"}.issubset(df.columns):
        lat = pd.to_numeric(df["lat"], errors="coerce")
        lon = pd.to_numeric(df["lon"], errors="coerce")
        in_bbox = lat.between(ALMATY_BBOX["lat_min"], ALMATY_BBOX["lat_max"]) & lon.between(
            ALMATY_BBOX["lon_min"], ALMATY_BBOX["lon_max"]
        )
        df = df[in_bbox | lat.isna() | lon.isna()]
    first_seen = pd.to_datetime(df["first_seen"], errors="coerce", utc=True)
    df = df[first_seen.isna() | (first_seen <= trained_at)]
    logger.info("Датасет as-of %s: %s объявлений", trained_at, len(df))
    return df.reset_index(drop=True)


def evaluate_era(
    era: dict[str, str],
    eras_dir: Path,
    test_df: pd.DataFrame,
    y_true: np.ndarray,
) -> dict[str, Any]:
    """Одна эпоха на общем фрейме → строка отчёта (metrics либо note-причина).

    Фейлы predict НЕ глушатся в NaN-метрики: как в гейте (old_model_error →
    fail-closed), эпоха с разошедшимся фиче-сетом честно исключается.
    """
    row: dict[str, Any] = {
        "commit": era["commit"], "date": era["date"], "label": era["label"],
        "n_features": None, "own_reported_mape": None,
        "mape": None, "mdape": None, "mae": None, "r2": None, "note": "",
    }
    era_dir = eras_dir / era["commit"]
    cbm_path = era_dir / "model.cbm"
    if not cbm_path.exists():
        row["note"] = f"исключена: артефакт не найден ({cbm_path})"
        return row

    meta_path = era_dir / "model_meta.json"
    if meta_path.exists():
        era_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        own = era_meta.get("metrics", {}).get("model", {}).get("mape")
        row["own_reported_mape"] = round(float(own), 6) if own is not None else None

    transform = era.get("target_transform", "log1p")
    if transform not in KNOWN_TRANSFORMS:
        row["note"] = f"исключена: неизвестный target_transform '{transform}'"
        return row

    model = CatBoostRegressor()
    model.load_model(str(cbm_path))
    feature_names = list(model.feature_names_)
    row["n_features"] = len(feature_names)
    missing = [name for name in feature_names if name not in test_df.columns]
    if missing:  # аналог old_model_error в гейте: не подсовываем нули молча
        row["note"] = f"исключена: во фрейме нет фичей {missing}"
        return row

    # Подмножество колонок общего фрейма в порядке feature_names_ модели
    pool = Pool(
        test_df[feature_names],
        cat_features=[name for name in feature_names if name in CAT_FEATURES],
    )
    y_pred = np.expm1(model.predict(pool))
    metrics = evaluate(y_true, y_pred)
    row.update({k: round(v, 6) for k, v in metrics.items()})
    return row


def to_markdown(rows: list[dict[str, Any]]) -> str:
    """Markdown-таблица сравнения: эпохи в хронологическом порядке + бейзлайн."""
    def pct(v: float | None) -> str:
        return f"{v:.2%}" if v is not None else "—"

    lines = [
        "| Модель | Дата | Фичей | MAPE тогда (свой тест) | MAPE | MdAPE | MAE | R² |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        name = f"`{r['commit']}` {r['label']}" if r.get("commit") else r["label"]
        if r["mape"] is None:
            note = r.get("note") or "исключена"
            lines.append(f"| {name} | {r['date'] or '—'} | {r['n_features'] or '—'} | "
                         f"{pct(r['own_reported_mape'])} | — | — | — | — {note} |")
            continue
        lines.append(
            f"| {name} | {r['date'] or '—'} | {r['n_features'] or '—'} "
            f"| {pct(r['own_reported_mape'])} | {pct(r['mape'])} | {pct(r['mdape'])} "
            f"| {r['mae'] / 1e6:.2f}M ₸ | {r['r2']:.4f} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Все исторические модели на одном свежем test-сплите (методика гейта)"
    )
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite база (лучше — замороженная копия)")
    parser.add_argument(
        "--eras-dir", required=True,
        help="Каталог с извлечёнными эпохами: <dir>/<commit>/model.cbm [+ model_meta.json]",
    )
    parser.add_argument(
        "--meta", default=str(MODEL_META_PATH),
        help="model_meta.json эпохи-якоря: trained_at, ppsm_maps и ожидания якорей",
    )
    parser.add_argument(
        "--spatial-ref", default=str(SPATIAL_REF_PATH),
        help="spatial_ref.json эпохи-якоря (H3-карты train-среза)",
    )
    parser.add_argument("--out", default=str(REPORTS_DIR / "model_comparison.json"))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr
    )
    # Консоль/редирект на Windows бывает cp1251: «₸»/«²» в таблице не должны
    # ронять print (и тем более подменять exit code уже записанного отчёта).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    anchor_meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    anchor_metrics = anchor_meta["metrics"]
    trained_at = pd.Timestamp(anchor_metrics["trained_at"])
    ppsm_maps = anchor_meta["ppsm_maps"]
    spatial_ref = json.loads(Path(args.spatial_ref).read_text(encoding="utf-8"))

    # --- Воспроизведение сплита эпохи-якоря (train.train, шаг за шагом) ---
    df = load_dataset_asof(args.db, trained_at)
    df = clean_listings(df)
    df = resolve_zones(df)
    df = dedup_relistings(df)
    dedup_stats = df.attrs.get("dedup_stats", {})
    logger.info("Дедуп: %s (в мете эпохи: %s)", dedup_stats, anchor_metrics.get("dedup"))

    train_idx, test_idx = time_based_split(df)
    if len(test_idx) == 0 or len(train_idx) == 0:
        raise SystemExit("Временной сплит не собрался — фрейм эпохи не воспроизведён")
    raw_train_all = df.iloc[train_idx].reset_index(drop=True)
    raw_test = df.iloc[test_idx].reset_index(drop=True)
    raw_train, n_purged = purge_leaked_train_rows(raw_train_all, raw_test)
    logger.info(
        "Сплит: n_train=%d (мета %s), n_test=%d (мета %s), purge=%d (мета %s)",
        len(raw_train), anchor_metrics.get("n_train"), len(raw_test),
        anchor_metrics.get("n_test"), n_purged, anchor_metrics.get("n_purged"),
    )

    # --- Единый фичефрейм: карты train-среза эпохи-якоря (как в гейте) ---
    test_df = build_features(raw_test, ppsm_maps=ppsm_maps, spatial_ref=spatial_ref)
    y_true = test_df["price"].to_numpy()
    test_days = pd.to_datetime(raw_test["first_seen"], errors="coerce", utc=True).dt.floor("D")
    test_window = f"{test_days.min():%Y-%m-%d}..{test_days.max():%Y-%m-%d}"
    # test_window — это span 14-дневного окна сплита, а не равномерные 14 дней:
    # фактическое распределение по дням кладём в отчёт, чтобы окно нельзя было
    # прочитать как «репрезентативные две недели рынка».
    day_counts = test_days.value_counts().sort_index()
    test_days_histogram = {f"{day:%Y-%m-%d}": int(n) for day, n in day_counts.items()}
    top3_share = float(day_counts.nlargest(3).sum() / len(test_df))

    # --- Эпохи + бейзлайн -------------------------------------------------
    rows: list[dict[str, Any]] = []
    for era in ERAS:
        row = evaluate_era(era, Path(args.eras_dir), test_df, y_true)
        rows.append(row)
        logger.info(
            "%s (%s): mape=%s%s", era["commit"], era["date"], row["mape"],
            f" [{row['note']}]" if row["note"] else "",
        )

    # Бейзлайн гейта: медиана ₸/м² (район × комнаты) c train-части сплита.
    # Как в train(): считается на ФИЧЕ-фрейме train (district после
    # resolve_zones/fillna, rooms санитизирован), не на сыром.
    train_df = build_features(
        raw_train, ppsm_maps=ppsm_maps, spatial_ref=spatial_ref,
        knn_self_indices=self_indices_for(raw_train),
    )
    baseline_metrics = evaluate(y_true, baseline_predict(train_df, test_df))
    baseline_row: dict[str, Any] = {
        "commit": None, "date": None,
        "label": "Бейзлайн: медиана ₸/м² (район × комнаты)",
        "n_features": None, "own_reported_mape": None,
        "note": "не модель; train-часть общего сплита",
        **{k: round(v, 6) for k, v in baseline_metrics.items()},
    }
    rows.append(baseline_row)

    # --- Валидация по якорям (критерий корректности всего пайплайна) ------
    by_commit = {r["commit"]: r for r in rows if r.get("commit")}
    cur_era, prev_era = ERAS[-1]["commit"], ERAS[-2]["commit"]
    anchors = [
        (f"{cur_era} (metrics.model)", anchor_metrics["model"]["mape"],
         by_commit.get(cur_era, {}).get("mape")),
        (f"{prev_era} (metrics.old_model)", anchor_metrics["old_model"]["mape"],
         by_commit.get(prev_era, {}).get("mape")),
        ("baseline (metrics.baseline)", anchor_metrics["baseline"]["mape"],
         baseline_row["mape"]),
    ]
    anchor_report = []
    anchors_ok = True
    for name, expected, actual in anchors:
        delta = abs(actual - expected) if actual is not None else float("inf")
        ok = delta <= ANCHOR_TOLERANCE
        anchors_ok &= ok
        anchor_report.append({
            "anchor": name, "expected_mape": round(expected, 6),
            "actual_mape": round(actual, 6) if actual is not None else None,
            "delta": round(delta, 6) if actual is not None else None, "ok": ok,
        })
        logger.info(
            "Якорь %s: ожидание %.6f, факт %s, |Δ| %s → %s", name, expected,
            f"{actual:.6f}" if actual is not None else "—",
            f"{delta:.6f}" if actual is not None else "—", "OK" if ok else "FAIL",
        )

    # Оговорки гардов ретрейна переносятся в отчёт как есть: тест ОДИН для всех
    # эпох, поэтому ранжирование честное, но абсолютные метрики нельзя читать
    # как «типичную ошибку по рынку» — сама мета помечает тест непрезентативным.
    caveats = {
        "test_days_histogram": test_days_histogram,
        "test_concentration": (
            f"{top3_share:.1%} тестовых лотов пришли за 3 самых плотных дня — "
            "test_window это span окна сплита, а не равномерные 14 дней"
        ),
        "test_representativeness": anchor_metrics.get("test_representativeness"),
        "temporal_validity": anchor_metrics.get("temporal_validity"),
        "time_confounding": {
            k: anchor_metrics.get("time_confounding", {}).get(k)
            for k in ("confounded", "worst_day_tvd", "threshold")
        },
        "note": (
            "тест общий для всех эпох — относительное ранжирование честное; "
            "абсолютные MAPE/MAE не читать как типичную ошибку по всему рынку "
            "(см. test_representativeness/temporal_validity выше)"
        ),
    }

    report = {
        "asof": trained_at.isoformat(),
        "test_window": test_window,
        "n_test": int(len(test_df)),
        "caveats": caveats,
        "excluded_checkpoints": EXCLUDED_CHECKPOINTS,
        "map_strategy": (
            f"единый фрейм: тест эпохи {trained_at:%Y-%m-%d}, фичи по картам её train-среза "
            "(ppsm_maps из model_meta.json, spatial_ref.json) — как гейт кормит "
            "старую модель картами нового train; era-карты моделям не выдаются"
        ),
        "anchors": anchor_report,
        "rows": rows,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Отчёт: %s", out_path)

    # Fail-closed ДО печати таблицы: exit code — гейт достоверности отчёта,
    # и падение print (экзотическая кодировка) не должно его маскировать.
    if not anchors_ok:
        logger.error("ЯКОРЯ НЕ СОШЛИСЬ — фрейм не воспроизведён, метрики недостоверны")
        sys.exit(1)

    print(f"\nОбщий тест: {test_window}, n_test={len(test_df)} (asof {trained_at})\n")
    print(to_markdown(rows))


if __name__ == "__main__":
    main()
