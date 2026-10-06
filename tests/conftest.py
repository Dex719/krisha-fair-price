"""Общие настройки тестов."""

import os

import pytest

# В тестах не скачиваем базу/модели из GitHub Release при старте приложения
# (TestClient триггерит startup-событие FastAPI).
os.environ.setdefault("KRISHA_DB_AUTO", "0")
os.environ.setdefault("KRISHA_MODEL_AUTO", "0")
# ...и не тянем состояние бота из репозитория данных.
os.environ.setdefault("KRISHA_STATE_PULL", "0")
# Флаш статистики в проде уходит в фоновый поток (см. usage._flush_async):
# в тестах это гонка «записалось ли уже», поэтому здесь — синхронно.
os.environ.setdefault("USAGE_FLUSH_SYNC", "1")
# Прогрев сессии krisha при старте приложения ходит в сеть — в тестах выключен
# (герметичный e2e-сервер наследует это окружение).
os.environ.setdefault("KRISHA_SESSION_WARMUP", "0")
# Живые цифры в разметке страниц (api/live_pages) при старте берутся из базы и
# модели на диске — в тестах разметка как в файлах; снимок подкладывают сами
# тесты, которым он нужен (test_seo_live_pages).
os.environ.setdefault("KRISHA_LIVE_PAGES", "0")


def pytest_runtest_setup(item):
    """`needs_model`: весов нет в git (issue #74) — они в приватном релизе.

    CI скачивает их по KRISHA_DB_TOKEN; без токена (PR от Dependabot без
    секрета, свежий клон) такие тесты пропускаются, а не падают на
    FileNotFoundError посреди предикта.
    """
    if item.get_closest_marker("needs_model"):
        from krisha.config import MODEL_PATH

        if not MODEL_PATH.exists():
            pytest.skip("нет models/model.cbm — скачай: python -m krisha.db_release --models")


@pytest.fixture(autouse=True)
def _isolate_repo_state(tmp_path, monkeypatch):
    """Статистика использования не должна попадать в data/ репозитория.

    Любой тест, открывающий страницу через TestClient, считает «визит», а с
    USAGE_FLUSH_SYNC=1 первый же визит флашится на диск — раньше прямо в боевой
    data/usage_stats.json рабочей копии, откуда его легко утащить коммитом
    (.kiro/specs/test-state-isolation). Пуш состояния в GitHub в тестах заглушен
    по той же причине; тесты самого пуша возвращают настоящую функцию явно.
    """
    from krisha import subscriptions, usage

    monkeypatch.setattr(usage, "USAGE_PATH", tmp_path / "usage_stats.json")
    monkeypatch.setattr(usage, "_state", None)
    monkeypatch.setattr(usage, "_last_flush", None)
    monkeypatch.setattr(subscriptions, "_push_to_github", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _clear_api_caches():
    """Кэши ответов API живут в модуле и переживают тест.

    Без сброса второй тест с тем же URL/базой получал бы ответ, посчитанный
    для первого (кэш предикта, свежести базы, статистики). Чистим до и после:
    порядок тестов не должен ничего значить.
    """
    from krisha import bot, predict_gate, text_parse
    from krisha.api import app as app_module
    from krisha.api import metrics

    caches = (
        app_module._freshness_cache,
        app_module._model_meta_cache,
        app_module._stats_cache,
        app_module._rent_stats_cache,
        app_module._heatmap_cache,
        app_module._forecast_cache,
        app_module._demo_pool_cache,
    )
    for cache in caches:
        cache.clear()
    # Кэш предикта и негативный кэш переехали в общую калитку (predict_gate):
    # её же используют веб и бот, значит и чистить надо там.
    predict_gate.clear()
    metrics.reset()
    # Счётчик rate-limit тоже общий: у TestClient один «IP» на все тесты, и
    # без сброса пятнадцатый запрос ЛЮБОГО теста получал 429 из-за соседей.
    app_module._rate.clear()
    # То же у бота (лимитер на чат) и у кэша разбора Gemini: оба живут в
    # модулях. Тесты, что шлют боту сообщения от одного и того же chat_id (42),
    # иначе копили бы общее ведро и однажды упёрлись в лимит соседей.
    bot.reset_rate_limits()
    text_parse._parse_cache.clear()
    yield
    for cache in caches:
        cache.clear()
    predict_gate.clear()
    metrics.reset()
    app_module._rate.clear()
    bot.reset_rate_limits()
    text_parse._parse_cache.clear()
