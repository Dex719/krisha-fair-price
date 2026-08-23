"""Общие настройки тестов."""

import os

import pytest

# В тестах не скачиваем базу/модели из GitHub Release при старте приложения
# (TestClient триггерит startup-событие FastAPI).
os.environ.setdefault("KRISHA_DB_AUTO", "0")
os.environ.setdefault("KRISHA_MODEL_AUTO", "0")
# Флаш статистики в проде уходит в фоновый поток (см. usage._flush_async):
# в тестах это гонка «записалось ли уже», поэтому здесь — синхронно.
os.environ.setdefault("USAGE_FLUSH_SYNC", "1")


@pytest.fixture(autouse=True)
def _clear_api_caches():
    """Кэши ответов API живут в модуле и переживают тест.

    Без сброса второй тест с тем же URL/базой получал бы ответ, посчитанный
    для первого (кэш предикта, свежести базы, статистики). Чистим до и после:
    порядок тестов не должен ничего значить.
    """
    from krisha.api import app as app_module

    caches = (
        app_module._predict_cache,
        app_module._freshness_cache,
        app_module._model_meta_cache,
        app_module._stats_cache,
        app_module._heatmap_cache,
        app_module._forecast_cache,
        app_module._demo_pool_cache,
    )
    for cache in caches:
        cache.clear()
    # Счётчик rate-limit тоже общий: у TestClient один «IP» на все тесты, и
    # без сброса пятнадцатый запрос ЛЮБОГО теста получал 429 из-за соседей.
    app_module._rate.clear()
    yield
    for cache in caches:
        cache.clear()
    app_module._rate.clear()
