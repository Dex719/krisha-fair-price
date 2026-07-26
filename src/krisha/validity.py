"""issue #158: честная оценка качества модели вместо одного числа.

Три отдельные вещи, которые часто путают.

1. НАСКОЛЬКО ПРЕДСТАВИТЕЛЕН ТЕСТ. Метрика измеряется на какой-то выборке, и
   без описания этой выборки число не значит почти ничего. На проде это не
   теория: сбор был ограничен лимитом 1000 объявлений в день и шёл по районам
   по алфавиту, поэтому «свежие дни» оказались почти одним районом — в
   текущем тесте 78.7% Алмалинского при TVD 0.46 от состава базы. Заявлять по
   такому тесту «средняя ошибка модели по Алматы» нельзя.

2. НАСКОЛЬКО ТОЧНО ИЗМЕРЕНА САМА МЕТРИКА. Одно число без интервала не даёт
   отличить улучшение от шума. Интервал строим КЛАСТЕРНЫМ бутстрепом по
   зданиям, а не построчным: квартиры в одном доме коррелируют (одна локация,
   один год постройки, часто один продавец), и построчный ресемплинг считает
   их независимыми наблюдениями — интервал выходит уже реального.

   Это отличается от парного бутстрепа в scripts/model_gate.py намеренно. Там
   сравниваются ДВЕ модели на одном тесте, разности APE берутся по одной и той
   же строке, и общая для обеих моделей сложность объекта сокращается. Здесь
   пары нет, сокращаться нечему, и кластеризация выходит на первый план.

3. НАСКОЛЬКО ВАЛИДЕН СПЛИТ ПО ВРЕМЕНИ. Rolling-origin меряет обобщение во
   времени только если состав данных не меняется вместе с временем. Если
   каждый следующий день — это другой район, backtest померяет перенос с
   дешёвого района на дорогой и назовёт это временной ошибкой.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from krisha.config import RANDOM_STATE

# Столько ресемплов хватает для перцентильного интервала: шум оценки границ
# становится заметно меньше самой ширины интервала. Столько же в model_gate.py.
BOOTSTRAP_N = 2000

# Доля кластеров, ниже которой интервал не строим. Кластерный бутстреп на
# горстке зданий даёт интервал, ширина которого сама по себе случайна.
MIN_CLUSTERS_FOR_CI = 30

# Total variation distance между составом теста и составом всей выборки, выше
# которой тест перестаёт представлять генеральную совокупность. 0.20 — это уже
# «каждый пятый объект не из того распределения»; на проде сейчас 0.46.
MAX_TEST_TVD = 0.20


def _canon(value) -> str:
    """Каноническое имя категории, одинаковое по обе стороны сравнения.

    Нужно из-за реального случая: одна сторона приходит после build_features,
    где rooms приведён к float, другая — сырая, где он int. Наивный
    astype(str) даёт «2.0» против «2», множества категорий не пересекаются
    вовсе, и TVD выходит 1.0 на любых данных. Диагностика, которая всегда
    кричит «непредставительно», ровно так же бесполезна, как та, что всегда
    молчит, — поэтому числа сводим к общему виду, а не к их текстовому
    представлению.
    """
    if value is None or pd.isna(value):
        return "?"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value).strip()


def total_variation_distance(part: pd.Series, whole: pd.Series) -> float:
    """TVD между распределениями категорий: 0 — совпадают, 1 — не пересекаются.

    Читается как «какую долю выборки нужно переложить, чтобы составы сошлись».
    """
    p = pd.Series(part).map(_canon).value_counts(normalize=True)
    q = pd.Series(whole).map(_canon).value_counts(normalize=True)
    keys = p.index.union(q.index)
    return float(0.5 * np.abs(p.reindex(keys).fillna(0) - q.reindex(keys).fillna(0)).sum())


def representativeness(
    test_df: pd.DataFrame, full_df: pd.DataFrame, columns: tuple[str, ...] = ("district", "rooms")
) -> dict:
    """Насколько состав теста похож на состав всей выборки.

    Возвращает TVD по каждой колонке, максимум из них и вердикт. Пишется в
    model_meta рядом с метриками: число обязано путешествовать вместе с
    описанием выборки, на которой получено.
    """
    per_column: dict[str, float] = {}
    for col in columns:
        if col not in test_df.columns or col not in full_df.columns:
            continue
        per_column[col] = total_variation_distance(test_df[col], full_df[col])
    worst = max(per_column.values(), default=0.0)
    return {
        "tvd": {k: round(v, 3) for k, v in per_column.items()},
        "worst_tvd": round(worst, 3),
        "representative": bool(worst <= MAX_TEST_TVD),
        "threshold": MAX_TEST_TVD,
    }


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """MAPE в долях. maximum(y,1) — та же защита от деления на ноль, что в
    _save_gate_samples: цена ноль это битая строка, а не бесконечная ошибка."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(y_pred - y_true) / np.maximum(y_true, 1.0)))


def cluster_bootstrap_ci(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    clusters: pd.Series | np.ndarray | None = None,
    *,
    confidence: float = 0.95,
    n_boot: int = BOOTSTRAP_N,
    seed: int = RANDOM_STATE,
) -> dict:
    """Перцентильный интервал MAPE кластерным бутстрепом по зданиям.

    Ресемплятся ЦЕЛЫЕ кластеры с возвращением, а не отдельные строки. Квартиры
    в одном доме коррелируют, и построчный ресемплинг завышает эффективный
    размер выборки — интервал получается уже реального, а решение «модель
    улучшилась» принимается на шуме.

    clusters=None — построчный бутстреп; допустимо только когда кластеров
    заведомо нет (синтетика в тестах). Возвращает point/lo/hi и n_clusters,
    чтобы вызывающий видел, на чём построен интервал.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    if len(y_true) == 0:
        return {"point": None, "lo": None, "hi": None, "n_clusters": 0, "reason": "пустой тест"}

    point = mape(y_true, y_pred)
    if clusters is None:
        groups = np.arange(len(y_true))
    else:
        groups = pd.Series(clusters).to_numpy()

    uniq, inverse = np.unique(groups, return_inverse=True)
    n_clusters = len(uniq)
    if n_clusters < MIN_CLUSTERS_FOR_CI:
        return {
            "point": round(point, 6),
            "lo": None,
            "hi": None,
            "n_clusters": int(n_clusters),
            "reason": (
                f"кластеров {n_clusters} < {MIN_CLUSTERS_FOR_CI} — интервал был бы "
                "случайнее самой оценки"
            ),
        }

    # Индексы строк каждого кластера считаем один раз: иначе на каждой из 2000
    # итераций пришлось бы фильтровать весь массив.
    order = np.argsort(inverse, kind="stable")
    sorted_inv = inverse[order]
    bounds = np.searchsorted(sorted_inv, np.arange(n_clusters + 1))
    rows_by_cluster = [order[bounds[i]:bounds[i + 1]] for i in range(n_clusters)]

    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        picked = rng.integers(0, n_clusters, size=n_clusters)
        idx = np.concatenate([rows_by_cluster[c] for c in picked])
        stats[b] = mape(y_true[idx], y_pred[idx])

    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.percentile(stats, [alpha * 100, (1 - alpha) * 100])
    return {
        "point": round(point, 6),
        "lo": round(float(lo), 6),
        "hi": round(float(hi), 6),
        "n_clusters": int(n_clusters),
        "confidence": confidence,
    }


def time_confounding(df: pd.DataFrame, day_col: str = "first_seen",
                     by: str = "district") -> dict:
    """Спутана ли временная ось с составом данных (issue #158).

    Rolling-origin меряет обобщение ВО ВРЕМЕНИ только если состав данных не
    меняется вместе с временем. На проде это условие нарушено грубо: сбор был
    ограничен лимитом 1000/день и шёл по районам по алфавиту, поэтому
    03–05.07 это на 97–99% Алатауский, а с 07.07 — на 69–87% Алмалинский.
    Backtest на таких данных померяет перенос с дешёвого района на дорогой и
    назовёт это ошибкой прогноза во времени.

    Возвращает TVD состава каждого дня против общего состава и вердикт
    confounded. Проверка нужна КАЖДЫЙ раз, а не однократно: она же
    автоматически перестанет срабатывать, когда сбор станет равномерным.
    """
    if day_col not in df.columns or by not in df.columns:
        return {"confounded": False, "reason": f"нет колонки {day_col} или {by}", "days": {}}
    days = pd.to_datetime(df[day_col], errors="coerce").dt.floor("D")
    usable = df[days.notna()]
    if usable.empty:
        return {"confounded": False, "reason": "нет дат", "days": {}}

    whole = usable[by]
    per_day: dict[str, float] = {}
    for day, sub in usable.groupby(days[days.notna()]):
        per_day[str(day.date())] = round(total_variation_distance(sub[by], whole), 3)
    worst = max(per_day.values(), default=0.0)
    return {
        "confounded": bool(worst > MAX_TEST_TVD),
        "worst_day_tvd": round(worst, 3),
        "threshold": MAX_TEST_TVD,
        "days": per_day,
    }
