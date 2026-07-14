"""issue #131: таргет-режимы точечной модели — сравнение на walk-forward стенде (#130).

Три варианта таргета (фичи ALL_FEATURES не меняются, меняется только цель
обучения и обратный переход к цене):

- ``"price"``          — log1p(price), текущий прод-таргет (``features.TARGET``).
                          predict: ``price = expm1(y_hat)``.
- ``"ppsm"``           — log(price/area). predict: ``price = exp(y_hat) * area``.
                          area остаётся в фичах — ₸/м² нелинейно зависит от площади.
- ``"index_residual"`` — log(price/area) - log(city_index(week(first_seen))).
                          predict: ``price = exp(y_hat) * city_index_now * area``.
                          Идея: дрейф уровня цен переносится из таргета в
                          city_index (пересчитывается по train), а не должен
                          угадываться моделью по district_ppsm/hex_ppsm — те
                          строятся один раз на train и «стареют» до следующего
                          ретрейна.

city_index — медиана ₸/м² по ФИКСИРОВАННОЙ корзине hex7-ячеек (отобранной
один раз по train, не переотбираемой по неделям — иначе смена состава
корзины маскировалась бы под движение цены), по календарным неделям
first_seen. index_now() — последняя известная неделя в train (то, что было
бы доступно на момент деплоя вплоть до следующего ретрейна).

Важно (issue #131, явное предупреждение): при использовании ppsm-таргетов
НЕ добавлять монотонное ограничение CatBoost по area — ₸/м² падает с ростом
площади, в отличие от полной цены. Ограничений в проекте сейчас нет вообще,
здесь только явная память для будущих изменений.

Известное ограничение (принято осознанно, не в скоупе issue #131): недельный
индекс считается по train, включающему саму строку, если она попала в
корзину (self-inclusion) — тот же паттерн, что и у уже существующих
district_ppsm/hex_ppsm (см. AUDIT.md, "target-encoding self-inclusion risk").
"""

from __future__ import annotations

from typing import Any

import h3
import numpy as np
import pandas as pd

TARGET_MODES = ("price", "ppsm", "index_residual")

HEX_INDEX_RES = 7          # тот же порядок, что и hex7_ppsm в krisha.spatial (~5 км²)
INDEX_MIN_CELL_N = 8       # порог по объявлениям в ячейке за весь train — как HEX_MIN_N в spatial.py

TARGET_COL_BY_MODE = {
    "price": "log_price",
    "ppsm": "log_ppsm",
    "index_residual": "log_ppsm_resid",
}


def target_col(mode: str) -> str:
    if mode not in TARGET_COL_BY_MODE:
        raise ValueError(f"неизвестный target mode: {mode!r}, ожидается один из {TARGET_MODES}")
    return TARGET_COL_BY_MODE[mode]


def _week_start(ts: pd.Series) -> pd.Series:
    """Начало ISO-недели (понедельник, UTC) — стабильный ключ группировки."""
    ts = pd.to_datetime(ts, utc=True, errors="coerce")
    return ts.dt.floor("D") - pd.to_timedelta(ts.dt.dayofweek, unit="D")


def build_city_index(
    train_raw: pd.DataFrame, hex_res: int = HEX_INDEX_RES, min_cell_n: int = INDEX_MIN_CELL_N
) -> dict[str, Any]:
    """Индекс рынка по train: фиксированная корзина hex7-ячеек + медиана ₸/м² по неделям first_seen.

    Корзина отбирается один раз по объявлениям за весь train (ячейки с
    >= min_cell_n объявлениями) — переотбор по неделям смешал бы изменение
    состава корзины с изменением уровня цен, что убило бы саму идею индекса.
    Нет валидных строк/пустая корзина -> ``weekly`` пуст, ``index_at``/
    ``index_now`` фолбэкают на глобальную медиану.
    """
    sub = train_raw.dropna(subset=["price", "area", "lat", "lon", "first_seen"]).copy()
    sub = sub[sub["area"] > 0]
    if sub.empty:
        return {"weekly": {}, "global": float("nan"), "basket_cells": 0}
    sub["ppsm"] = sub["price"] / sub["area"]
    global_median = float(sub["ppsm"].median())

    lat = sub["lat"].astype(float).to_numpy()
    lon = sub["lon"].astype(float).to_numpy()
    sub["_cell"] = [h3.latlng_to_cell(la, lo, hex_res) for la, lo in zip(lat, lon)]
    cell_counts = sub["_cell"].value_counts()
    basket = set(cell_counts[cell_counts >= min_cell_n].index)
    if not basket:
        return {"weekly": {}, "global": global_median, "basket_cells": 0}

    in_basket = sub[sub["_cell"].isin(basket)].copy()
    in_basket["_week"] = _week_start(in_basket["first_seen"])
    in_basket = in_basket.dropna(subset=["_week"])
    weekly = in_basket.groupby("_week")["ppsm"].median().sort_index()
    return {
        "weekly": {ts: float(v) for ts, v in weekly.items()},
        "global": global_median,
        "basket_cells": len(basket),
    }


def index_at(index_ref: dict[str, Any], ts: Any) -> float:
    """Индекс для произвольного момента: своя неделя, иначе последняя известная
    неделя строго раньше неё, иначе глобальная медиана (истории раньше ts нет)."""
    weekly: dict = index_ref.get("weekly") or {}
    if not weekly:
        return float(index_ref.get("global", float("nan")))
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("utc")
    wk = ts.floor("D") - pd.Timedelta(days=ts.dayofweek)
    if wk in weekly:
        return weekly[wk]
    earlier = [w for w in weekly if w <= wk]
    if earlier:
        return weekly[max(earlier)]
    return float(index_ref.get("global", float("nan")))


def index_now(index_ref: dict[str, Any]) -> float:
    """Индекс «на сейчас» — последняя известная неделя в train.

    В проде индекс задуман обновляемым чаще ретрейна модели (issue #131:
    «оценка едет с рынком между еженедельными ретрейнами») — здесь, на
    walk-forward стенде, доступен только train-срез фолда, это честное
    приближение того, что видел бы сервис на момент прогноза.
    """
    weekly: dict = index_ref.get("weekly") or {}
    if not weekly:
        return float(index_ref.get("global", float("nan")))
    return weekly[max(weekly.keys())]


def add_target_column(
    df: pd.DataFrame, mode: str, index_ref: dict[str, Any] | None = None
) -> tuple[pd.DataFrame, str]:
    """Добавляет колонку-таргет для режима `mode`, возвращает (df, имя_колонки).

    Ожидает df после build_features (есть price/area/first_seen — они
    проходят через build_features как есть, log_price уже посчитан там).
    """
    col = target_col(mode)
    df = df.copy()
    if mode == "price":
        if col not in df:  # build_features уже считает log_price, если price есть
            df[col] = np.log1p(df["price"])
        return df, col

    ppsm = df["price"] / df["area"].replace(0, np.nan)
    if mode == "ppsm":
        df[col] = np.log(ppsm)
        return df, col

    # index_residual
    if index_ref is None:
        raise ValueError("index_residual требует index_ref (build_city_index по train)")
    idx = df["first_seen"].map(lambda ts: index_at(index_ref, ts))
    df[col] = np.log(ppsm) - np.log(idx.astype(float))
    return df, col


def predict_price(
    y_hat: np.ndarray,
    area: pd.Series | np.ndarray,
    mode: str,
    index_ref: dict[str, Any] | None = None,
) -> np.ndarray:
    """Обратный переход из предсказания модели (в пространстве таргета `mode`) в цену.

    Для index_residual используется единый снэпшот city_index_now по всему
    вызову (одно значение на весь тестовый фолд) — так предсказывала бы
    прод-система между ретрейнами, а не по недели каждой строки теста
    (которую на практике модель ещё не видела/не знает вперёд).
    """
    y_hat = np.asarray(y_hat, dtype=float)
    area = np.asarray(area, dtype=float)
    if mode == "price":
        return np.expm1(y_hat)
    if mode == "ppsm":
        return np.exp(y_hat) * area
    if mode == "index_residual":
        if index_ref is None:
            raise ValueError("index_residual требует index_ref")
        return np.exp(y_hat) * index_now(index_ref) * area
    raise ValueError(f"неизвестный target mode: {mode!r}")


def freshness_weight(
    first_seen: pd.Series, as_of: Any, half_life_days: float
) -> np.ndarray:
    """Вес по свежести: 0.5 ** (age_days / half_life_days).

    age_days — возраст объявления (first_seen) относительно `as_of`
    (граница фолда, откуда «смотрит» train), отрицательный возраст
    (first_seen в будущем относительно as_of — не должно случаться при
    честном сплите) клипается в 0, чтобы не давать вес > 1.
    """
    as_of = pd.Timestamp(as_of)
    fs = pd.to_datetime(first_seen, utc=True, errors="coerce")
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("utc")
    age_days = (as_of - fs).dt.total_seconds() / 86400.0
    age_days = age_days.clip(lower=0).fillna(0)
    return np.power(0.5, age_days / half_life_days).to_numpy()
