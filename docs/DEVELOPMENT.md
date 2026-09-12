# Разработка

## Окружение

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                # runtime + train (shap, matplotlib, sklearn) + pytest, ruff, pytest-playwright
python -m krisha.db_release --require                  # прод-база → data/krisha.db (не собирать самому)
make api                                               # http://localhost:8000, --reload
```

Python 3.11 (Dockerfile пинит `3.11.15-slim` по digest). Пакет ставится `-e`, поэтому `config.ROOT_DIR` указывает на корень репозитория: `data/`, `models/`, `static/` берутся оттуда. Модели уже в git — предикт работает сразу; `KRISHA_DB_AUTO=0` отключает автоскачивание базы при старте, если хочется пустую.

Локи: `requirements.lock` (runtime+dev под Linux, ставится в CI и Docker), `requirements-train.lock` (ретрейн), `requirements-runtime.lock` (= `requirements.lock` минус plotting-хвост catboost, ставится в образ с `--no-deps`). После правки зависимостей в `pyproject.toml` — `make lock` и все три файла в тот же PR; `tests/test_runtime_lock.py` проверяет их согласованность. На Windows/macOS локи не ставить — `pip install -e ".[dev]"`.

## Команды

| Команда | Что |
|---|---|
| `make test` | `pytest -q` — юниты и приёмка, без e2e/live (`addopts = -m 'not e2e and not live'`), ~2 мин |
| `make test-e2e` | `pytest -m e2e` — uvicorn + Playwright Chromium, сеть замокана; заранее `playwright install chromium` |
| `pytest -m live` | один живой запрос к krisha.kz и реальная база — только руками |
| `make lint` | `ruff check src tests scripts` (E, F, I, W, B; line-length 100; E501/B905 выключены) |
| `make crawl` / `make crawl-full` | разовый краул 50 лотов / ~300 страниц |
| `make rescrape` | локальный проход `scripts/rescrape.py` (все шарды, вежливые паузы — часы) |
| `make train` | `scripts/train.py` → `models/*`, `reports/shap_summary.png` |
| `make crawl-complexes` | разовый скрейп каталога ЖК |
| `python scripts/backtest.py` | walk-forward сравнение вариантов модели |
| `python scripts/loadtest.py <url>` | нагрузочный замер публичных ручек (не `/api/predict`) |
| `python scripts/smoke_prod.py` | смоук прода локально |

## Тесты

673 теста в `tests/` (57 из них e2e), фикстуры реальной разметки — `tests/fixtures/`. Организация по модулям: `test_detail_parser`, `test_db`, `test_features`, `test_train_smoke` (обучение на синтетике), `test_model_artifacts` (мета и модели в репозитории согласованы), `test_api_*` (схема, безопасность, нагрузка), `test_bot`, `test_drain_mode`, `test_data_contracts`, приёмка фронта `test_home_*`/`test_about_page`, `test_probes_and_seo`, README-метрики (`sync_readme_metrics --check`).

Что считается достаточным покрытием для PR:

- Парсер — фикстура реальной страницы + тест на каждое новое поле.
- Схема БД — миграция в `_migrate()` + тест, что `init_db()` на старой базе добавляет колонку.
- Фича модели — тест в `test_features.py` на `build_features` **и** `listing_to_frame` (train и predict должны считать одинаково) + прогон `test_model_artifacts`.
- Ручка API — тест схемы (`test_api_schema`) и, если есть кэш/лимит, `test_api_load_hardening`.
- Фронт — приёмочный тест на присутствие элементов (`test_home_*`), e2e при изменении поведения.
- Воркфлоу — прогон `workflow_dispatch` в ветке или dry-run скрипта (`send_alerts.py --dry-run`, `backfill_gap_cohort.py --dry-run`).

Тесты не ходят в сеть и не требуют прод-базы: базы создаются в `tmp_path`, HTTP-клиент и `tg_call` подменяются `monkeypatch` в самих тестах, `conftest.py` сбрасывает кэши API между тестами. Если тест захотел интернет — это ошибка теста.

## Как устроен код: конвенции

- **Ссылка на issue в комментарии** (`issue #152`, `аудит, находка #6`) — стандарт. Комментарии объясняют *почему* (замер, инцидент, отвергнутая альтернатива), не *что*. Перед удалением «странного» кода — прочитать issue.
- **Fail-soft на пользовательском пути, fail-loud в конвейере.** Предикт без интервала, без аналогов, без Gemini — всё равно отдаёт вердикт; рескрейп с нулём лотов — падает с ненулевым кодом. Новый код должен попадать в одну из этих категорий явно.
- **Никаких походов во внешние сервисы на предикте без фича-флага** (`config.feature_*`), дефолт — выключено.
- **Всё измеримое — с числом.** Изменение производительности — `loadtest.py` до/после в описании PR и CHANGELOG; изменение модели — `backtest.py`; изменение сбора — `sweep_runs` за несколько дней.
- **`config.py` — листовой модуль** (ничего из `krisha.*` не импортирует). Маппинги районов и пороги живут там и реэкспортируются, а не копируются.
- **`db.py` лёгкий**: на hot path API без pandas/numpy. Тяжёлые вычисления — в `zones.py`, `spatial.py`, `stats.py`.
- **Один код в train и predict** для всего, что влияет на метрику (`interval.py`, `features.py`): гейт должен мерить то, что видит пользователь.
- Стиль: ruff, русские докстринги и комментарии, английские идентификаторы; ширина строки 100.

## Git и релизы

- Ветка от `main`, PR, зелёный CI (юниты + e2e + pip-audit), мерж. Деплой автоматический после мержа; смоук подтверждает ревизию.
- Коммиты ботов: `github-actions[bot]` (модели по воскресеньям), `data: …` (state-файлы из Space) — не ребейзить поверх них руками, не редактировать `data/*.json` в PR.
- `.github/workflows/*` через GitHub App не пушатся — правки воркфлоу уходят с локальной машины (`gh`/PAT со scope `workflow`).
- Релиз (`CHANGELOG.md`): поднять версию в `pyproject.toml` и `src/krisha/__init__.py`, обновить подвалы `static/index.html` и `static/stats.html`, добавить запись в CHANGELOG, после мержа — тег `vX.Y.Z`. Semver `0.MINOR.PATCH`: MINOR за фичи, PATCH за фиксы. Текущая версия — `/api/health` и бейдж README.
- Лицензия кода — Elastic License 2.0; данные и модели ею не покрываются.

## Структура репозитория

```
src/krisha/            пакет (см. ARCHITECTURE.md — назначение каждого модуля)
  api/                 FastAPI: app, cache, static_cache, metrics, schemas
  scraping/            client, парсеры, crawler, rescrape, pass_plan, shard_plan
scripts/               CLI конвейера: crawl, rescrape, train, model_gate, send_alerts, smoke_prod, …
tests/  tests/e2e/     pytest; фикстуры реальной разметки в tests/fixtures/
static/                фронт: index/stats/about/404.html, design.css, js/, fonts/, img/
models/                артефакты модели и снапшоты справочников (коммитятся ретрейном)
data/                  krisha.db (не в git, из релиза) и state-файлы *.json (в git)
reports/               shap_summary.png, model_comparison.json
docs/                  эта документация, ROADMAP, аудиты, логотипы, tg-proxy-worker.js
Dockerfile Makefile pyproject.toml requirements*.lock CHANGELOG.md
```
