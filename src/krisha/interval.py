"""Финализация доверительного интервала цены — общая для train и predict.

issue #105 (доп.): границы интервала свопались-при-пересечении и
растягивались-под-точку в predict.py, но НЕ в train.py при подсчёте
coverage_test — метрический гейт (#106) мерил покрытие интервала, который
реально не отдаётся пользователю. Один код в обоих местах.
"""

from __future__ import annotations

import numpy as np

# issue #105 (доп., нормированный CQR): пол ширины интервала при нормировке
# conformity-скора — недообученная/выродившаяся квантильная модель может дать
# hi≈lo (или hi<lo) на отдельных строках, и без пола нормированный скор
# взрывается. Живёт здесь (не в train.py), чтобы predict.py — лёгкий
# сервинг-путь без sklearn/shap — мог импортировать константу без тяжёлых
# training-зависимостей.
MIN_INTERVAL_WIDTH_LOG = 0.05


def finalize_interval(point, low, high):
    """Приводит (low, high) к валидному интервалу вокруг точки.

    1. Числовая страховка: если границы перепутаны (low > high) — свопаем.
    2. Точка-оценка всегда внутри интервала: интервал только расширяем,
       никогда не сужаем.

    Работает и на скалярах (predict.py, один листинг), и на np.ndarray
    (train.py, весь test-сплит сразу).
    """
    point = np.asarray(point, dtype=float)
    low = np.asarray(low, dtype=float)
    high = np.asarray(high, dtype=float)
    low, high = np.minimum(low, high), np.maximum(low, high)
    low = np.minimum(low, point)
    high = np.maximum(high, point)
    if low.ndim == 0:
        return float(low), float(high)
    return low, high
