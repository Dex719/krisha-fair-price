"""issue #158: валидность измерения — представительность, интервал, спутанность."""

import numpy as np
import pandas as pd
import pytest

from krisha.validity import (
    MAX_TEST_TVD,
    MIN_CLUSTERS_FOR_CI,
    cluster_bootstrap_ci,
    representativeness,
    time_confounding,
    total_variation_distance,
)

# --- TVD -------------------------------------------------------------------

def test_identical_distributions_give_zero():
    s = pd.Series(["a", "a", "b", "c"])
    assert total_variation_distance(s, s) == 0.0


def test_disjoint_distributions_give_one():
    assert total_variation_distance(pd.Series(["a"] * 5), pd.Series(["b"] * 5)) == 1.0


def test_int_float_and_string_encodings_of_same_values_match():
    """Регрессия. rooms приходит float после build_features и int из сырого
    датафрейма; наивный astype(str) давал «2.0» против «2», множества
    категорий не пересекались, и TVD выходил 1.0 на ЛЮБЫХ данных.

    Диагностика, которая всегда кричит «непредставительно», ровно так же
    бесполезна, как та, что всегда молчит: на проде она показывала worst_tvd
    1.0 при настоящем значении 0.181.
    """
    ints = pd.Series([1, 2, 2, 3])
    floats = pd.Series([1.0, 2.0, 2.0, 3.0])
    strings = pd.Series(["1", "2", "2", "3"])

    assert total_variation_distance(ints, floats) == 0.0
    assert total_variation_distance(ints, strings) == 0.0


def test_missing_values_are_their_own_category():
    """NaN — «неизвестно», а не совпадение с чем угодно. Тест целиком из строк
    без района не представителен, и молчать об этом нельзя."""
    assert total_variation_distance(pd.Series([None, None]), pd.Series(["a", "b"])) == 1.0


# --- Представительность ----------------------------------------------------

def test_representative_sample_passes():
    full = pd.DataFrame({"district": ["a"] * 50 + ["b"] * 50, "rooms": [1] * 50 + [2] * 50})

    result = representativeness(full.sample(40, random_state=0), full)

    assert result["representative"] is True
    assert result["worst_tvd"] <= MAX_TEST_TVD


def test_single_district_sample_is_flagged():
    """Реальный случай прода: сбор шёл по районам по алфавиту с лимитом
    1000/день, поэтому «свежие дни» оказались почти одним районом — в тесте
    79% Алмалинского при TVD 0.46. Заявлять по такому тесту среднюю ошибку
    модели по городу нельзя, и ранжировать по нему две модели тоже.
    """
    full = pd.DataFrame({"district": ["a"] * 50 + ["b"] * 50, "rooms": [2] * 100})
    test = full[full["district"] == "b"].head(30)

    result = representativeness(test, full)

    assert result["representative"] is False
    assert result["tvd"]["district"] == pytest.approx(0.5, abs=0.01)


def test_missing_columns_are_skipped_not_fatal():
    """Диагностика не должна ронять обучение на старой базе или синтетике."""
    full = pd.DataFrame({"price": [1, 2, 3]})

    result = representativeness(full, full)

    assert result["tvd"] == {} and result["representative"] is True


# --- Интервал --------------------------------------------------------------

def _clustered(n_clusters=200, per_cluster=10, seed=0):
    """Данные с эффектом здания: общая для дома добавка к ошибке."""
    rng = np.random.default_rng(seed)
    clusters = np.repeat(np.arange(n_clusters), per_cluster)
    effect = rng.normal(0, 0.10, n_clusters).repeat(per_cluster)
    y_true = rng.uniform(2e7, 8e7, n_clusters * per_cluster)
    y_pred = y_true * (1 + effect + rng.normal(0, 0.03, len(y_true)))
    return y_true, y_pred, clusters


def test_interval_contains_point_estimate():
    y_true, y_pred, clusters = _clustered()

    ci = cluster_bootstrap_ci(y_true, y_pred, clusters=clusters)

    assert ci["lo"] < ci["point"] < ci["hi"]
    assert ci["n_clusters"] == 200


def test_cluster_bootstrap_is_wider_than_naive():
    """Главное свойство. Квартиры одного дома коррелируют; построчный
    ресемплинг считает их независимыми, завышает эффективный размер выборки и
    даёт интервал уже реального — то есть фабрику ложных «улучшений».
    """
    y_true, y_pred, clusters = _clustered()

    naive = cluster_bootstrap_ci(y_true, y_pred, clusters=None)
    clustered = cluster_bootstrap_ci(y_true, y_pred, clusters=clusters)

    assert (clustered["hi"] - clustered["lo"]) > (naive["hi"] - naive["lo"]) * 1.5


def test_refuses_interval_on_too_few_clusters():
    """На горстке зданий ширина интервала сама по себе случайна — честнее
    отдать оценку без интервала, чем интервал, которому нельзя верить."""
    y_true, y_pred, clusters = _clustered(n_clusters=5, per_cluster=10)

    ci = cluster_bootstrap_ci(y_true, y_pred, clusters=clusters)

    assert ci["point"] is not None
    assert ci["lo"] is None and ci["hi"] is None
    assert str(MIN_CLUSTERS_FOR_CI) in ci["reason"]


def test_empty_test_set_does_not_raise():
    ci = cluster_bootstrap_ci(np.array([]), np.array([]))

    assert ci["point"] is None and ci["n_clusters"] == 0


def test_interval_is_deterministic():
    """Один и тот же сплит обязан давать один и тот же интервал: иначе
    сравнение релизов модели превращается в лотерею."""
    y_true, y_pred, clusters = _clustered()

    assert cluster_bootstrap_ci(y_true, y_pred, clusters=clusters) == cluster_bootstrap_ci(
        y_true, y_pred, clusters=clusters
    )


# --- Спутанность времени с составом ---------------------------------------

def test_uniform_collection_is_not_confounded():
    """Каждый день собираем оба района поровну — время не спутано с составом,
    rolling-origin осмыслен. Это состояние, к которому ведёт круговая очередь
    докачки (#152); гард обязан САМ погаснуть, когда сбор выровняется."""
    df = pd.DataFrame({
        "first_seen": pd.date_range("2026-08-01", periods=10).repeat(20),
        "district": ["a", "b"] * 100,
    })

    assert time_confounding(df)["confounded"] is False


def test_alphabetical_crawl_is_detected_as_confounded():
    """Реальный случай прода: 03–05.07 на 97–99% Алатауский, с 07.07 — на
    69–87% Алмалинский, потому что краулер шёл по районам по алфавиту с
    лимитом 1000/день. Rolling-origin на таких данных померяет перенос с
    дешёвого района на дорогой и подпишет это «ошибкой прогноза во времени».
    """
    early = pd.DataFrame({
        "first_seen": pd.date_range("2026-07-03", periods=3).repeat(50),
        "district": ["a"] * 150,
    })
    late = pd.DataFrame({
        "first_seen": pd.date_range("2026-07-07", periods=3).repeat(50),
        "district": ["b"] * 150,
    })

    result = time_confounding(pd.concat([early, late], ignore_index=True))

    assert result["confounded"] is True
    assert result["worst_day_tvd"] == pytest.approx(0.5, abs=0.01)


def test_missing_date_column_is_not_fatal():
    result = time_confounding(pd.DataFrame({"district": ["a", "b"]}))

    assert result["confounded"] is False and "нет колонки" in result["reason"]


# --- Вердикт спутанности: взвешенное среднее вместо максимума --------------

def test_single_tiny_day_does_not_condemn_even_collection():
    """Раньше вердикт брал МАКСИМУМ по всем дням. День с горсткой строк даёт
    высокий TVD от одного размера выборки (девять районов в двадцати лотах не
    выпадут), и метрика не могла позеленеть в принципе — а гейт релизов на
    такой метрике блокировал бы модель за прошлое, а не за качество."""
    even = pd.DataFrame({
        "first_seen": pd.date_range("2026-08-01", periods=10).repeat(100),
        "district": ["a", "b"] * 500,
    })
    tiny = pd.DataFrame({
        "first_seen": [pd.Timestamp("2026-08-11")] * 3,
        "district": ["a", "a", "a"],
    })

    result = time_confounding(pd.concat([even, tiny], ignore_index=True))

    assert result["confounded"] is False
    assert result["skipped_small_days"] == 1
    assert result["worst_day_tvd_all_days"] >= 0.4  # мелкий день виден в диагностике


def test_old_skew_stops_hiding_current_collection():
    """Перекошенная когорта из прошлого остаётся в выборке навсегда. Вопрос
    «валидна ли ОЦЕНКА СЕЙЧАС» решается окном, а не всей историей."""
    july = pd.DataFrame({
        "first_seen": pd.date_range("2026-07-01", periods=5).repeat(200),
        "district": ["a"] * 1000,
    })
    august = pd.DataFrame({
        "first_seen": pd.date_range("2026-08-10", periods=5).repeat(200),
        "district": ["a", "b"] * 500,
    })
    df = pd.concat([july, august], ignore_index=True)

    assert time_confounding(df)["confounded"] is True
    assert time_confounding(df, window_days=20)["confounded"] is False


def test_weighted_verdict_reacts_to_where_the_data_is():
    """Взвешивание по размеру дня: перекос считается там, где лежат данные.

    Половина выборки в дни «только район a», половина — «только район b»:
    состав меняется вместе с временем на всей массе данных, и вердикт обязан
    быть красным.
    """
    first = pd.DataFrame({
        "first_seen": pd.date_range("2026-08-01", periods=3).repeat(400),
        "district": ["a"] * 1200,
    })
    second = pd.DataFrame({
        "first_seen": pd.date_range("2026-08-04", periods=3).repeat(400),
        "district": ["b"] * 1200,
    })

    result = time_confounding(pd.concat([first, second], ignore_index=True))

    assert result["confounded"] is True
    assert result["weighted_day_tvd"] == pytest.approx(0.5, abs=0.01)
    assert result["evaluated_days"] == 6


def test_small_tail_of_odd_days_does_not_flip_the_verdict():
    """Обратная сторона взвешивания, названная явно: пара нетипичных дней на
    краю выборки вердикт не переворачивает. Сдвиг состава ИМЕННО в тестовом
    окне ловит не эта проверка, а representativeness (тест против всей
    выборки) — они дополняют друг друга, поэтому temporal_validity требует
    обеих сразу."""
    bulk = pd.DataFrame({
        "first_seen": pd.date_range("2026-08-01", periods=5).repeat(400),
        "district": ["a", "b"] * 1000,
    })
    tail = pd.DataFrame({
        "first_seen": pd.date_range("2026-08-06", periods=2).repeat(40),
        "district": ["a"] * 80,
    })

    result = time_confounding(pd.concat([bulk, tail], ignore_index=True))

    assert result["confounded"] is False
    assert result["worst_day_tvd"] > result["threshold"]  # сами дни видны в отчёте
