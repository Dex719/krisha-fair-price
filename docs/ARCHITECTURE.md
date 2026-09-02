# Архитектура

## Одним абзацем

baǵam — это одно Python-приложение (`src/krisha/`) с тремя ролями: **сборщик** (краулер и ежедневный рескрейп krisha.kz в SQLite), **тренер** (CatBoost + квантильная модель + отчёты) и **сервис** (FastAPI: сайт, JSON API и Telegram-бот через webhook). Сборщик и тренер работают внутри GitHub Actions на чистых раннерах, сервис — в Docker-контейнере на Hugging Face Spaces. Состояние между ними передаётся через GitHub Releases (база и модели) и коммиты в репозиторий (модели после ретрейна, state-файлы бота).

## Компоненты и где они исполняются

```
                 ┌─────────────────────── GitHub Actions ───────────────────────┐
                 │  rescrape.yml (ежедн.)   retrain.yml (еженед.)   ci.yml       │
krisha.kz ──────▶│  scripts/rescrape.py  ─▶ scripts/train.py     ─▶ pytest/ruff  │
                 │        │   send_alerts.py      │  model_gate.py      │        │
                 └────────┼──────────────────────┼─────────────────────┼────────┘
                          ▼                      ▼                     ▼
              GitHub Release db-latest    git: models/*, README   deploy-hf.yml
              (krisha.db.gz + .sha256)    Release model-latest        │
                          │                      │                    ▼
                          └──────────┬───────────┘        Hugging Face Space (Docker)
                                     ▼                    uvicorn × WEB_CONCURRENCY=2
                        при старте: db_release.ensure_db()  ┌───────────────────────┐
                                    ensure_models()         │ FastAPI krisha.api.app│
                                                            │  /  /stats  /about    │
        браузер ◀──────────────────────────────────────────▶│  /api/predict …       │
        Telegram ◀────── POST /tg/webhook ─────────────────▶│  krisha.bot           │
                                                            └───────────┬───────────┘
                          state-файлы data/*.json ◀── Contents API ─────┘
                          (subscriptions, tracked, usage, prediction_log, channel_posted)
```

### Сервис (`src/krisha/api/`, `src/krisha/bot.py`)

- `api/app.py` — всё приложение: маршруты, middleware, startup. Startup идёт под файловым `flock`, потому что воркеров два: базу качает и мигрирует один, второй ждёт. Затем прогрев кэшей (модели, spatial/OSM снапшоты, статистика) и регистрация webhook бота.
- `api/cache.py` — `TTLCache`: TTL + single-flight (per-key lock) + отдача протухшего значения при сбое пересчёта. Все «дорогие» ответы (`/api/stats`, `/api/heatmap`, `/api/health`, demo-пул) ходят через него.
- `api/static_cache.py` — статика читается и сжимается один раз при старте, ETag от содержимого. Причина — замер: сжатие на лету стоило половины пропускной способности главной.
- `api/metrics.py` — счётчики горячего пути в памяти процесса (rps, коды, p50/p95/p99) → `/api/metrics`. Per-process: при двух воркерах показывает статистику того, кто ответил.
- `predict_gate.py` — единая калитка к предикту для сайта и бота: кэш по id лота, single-flight, негативный кэш битых ссылок, лимит одновременных скрейпов, короткий сетевой бюджет (5 с вместо краулерных 30), 503 + `Retry-After` вместо висящего спиннера.
- `bot.py` — Telegram через webhook на том же процессе. Дедуп апдейтов между воркерами — таблица `tg_updates` в SQLite.

### Предикт (`predict.py` и вокруг)

`predict_from_url` → `scraping.detail_parser` (одна детальная страница) → `features.listing_to_frame` (те же фичи, что в train, плюс снапшоты `complexes.json`, `osm_pois.json`, `osm_zones.json`, `spatial_ref.json`) → точечная модель `model.cbm` → интервал `model_quantile.cbm` + CQR-калибровка из меты → вердикт по интервалу → SHAP top-факторы с подсказками (`factor_hints.py`) → обвязка карточки: аналоги (`analogs.py`), история цены и ликвидность (`market.py`), бейдж «подозрительно дёшево» (`scam.py`), опционально ремонт по фото (`vision.py`, за флагом) и LLM-флаги описания (`llm_flags.py`, только из кэша без ключа).

Результат при `PREDICTION_LOG=1` дописывается в `data/prediction_log.json` (`prediction_log.py`) — единственный способ потом сверить вердикт с судьбой лота, потому что таблица `predictions` в SQLite стирается вместе с контейнером.

### Сборщик (`src/krisha/scraping/`)

- `client.py` — `PoliteClient`: паузы из `REQUEST_DELAY_RANGE`, ретраи, единый User-Agent, детект бана.
- `listing_parser.py` / `detail_parser.py` / `complex_parser.py` / `json_window.py` — парсеры. Главный источник данных — инлайн `window.data = {...}`; HTML-блоки `data-name` — вторичный. При смене вёрстки чинить сначала селекторы PARAMS, `window.data` стабилен.
- `crawler.py` — разовый возобновляемый краул (`scripts/crawl.py`), исторически первый способ набрать базу.
- `rescrape.py` — ежедневный проход `sweep()`: выдача по 32 шардам «район × комнаты» → сайтинги, изменения цен, детект снятий, докачка деталей новых и устаревших. Подробно в [DATA.md](DATA.md).
- `pass_plan.py` — режим прохода (drain/steady) по размеру очереди и бюджет времени; `shard_plan.py` — квоты докачки по шардам пропорционально стоку.

### Тренер (`train.py`, `scripts/train.py`, `scripts/model_gate.py`)

Загрузка базы → фильтры адекватности и bbox Алматы → дедуп перевыставлений по fingerprint → временно́й сплит по `first_seen` с исключением bulk-дней → purge утечек train→test → CatBoost RMSE на `log1p(price)` с early stopping → MultiQuantile q10/q90 + CQR → метрики, ДИ MAPE (кластерный бутстрэп), репрезентативность теста, временна́я валидность → `models/model_meta.json`, `stats.json`, `spatial_ref.json`, SHAP-отчёт. Подробно в [MODEL.md](MODEL.md).

### Данные и Telegram-обвязка

`db.py` — схема и upsert (всегда обновляются `price`, `title`, `raw_params`; остальное через `COALESCE`, чтобы неполный парс не затирал хорошие данные). `db_release.py` — скачивание/проверка sha256 базы и моделей из релизов. `subscriptions.py`, `tracking.py`, `alerts.py`, `channel.py`, `usage.py`, `daily_report.py`, `report.py`, `monitoring.py` — всё, что после рескрейпа шлёт сообщения и хранит состояние в JSON-файлах репозитория.

## Потоки данных

**Ночь (01:00 UTC, `rescrape.yml`).** Раннер скачивает `db-latest` → `rescrape.py` проходит выдачу и докачивает детали → `send_alerts.py` (выгодные лоты подписчикам, алерты `/track`, топ-5 в канал, утренний админ-отчёт, по понедельникам — статистика использования, 1-го числа — месячный отчёт) → база `VACUUM`, gzip, sha256 → загрузка в `db-latest` → рестарт Space (если есть `HF_TOKEN`) → датированный `snapshot-YYYY-MM-DD` релиз.

**Воскресенье (02:00 UTC, `retrain.yml`).** База из релиза → `train.py --compare-old` (старая модель оценивается на том же тесте) → `model_gate.py` (парный бутстрэп APE; хуже сверх допуска → exit 1, модель не коммитится) → Telegram-отчёт → `sync_readme_metrics.py` → коммит `models/`, `reports/`, README → `gh workflow run deploy-hf.yml` → `models.tar.gz` в `model-latest`.

**Push в main.** `ci.yml` (ruff, pytest, pip-audit по локу; отдельный job — герметичные e2e в Chromium) и параллельно `deploy-hf.yml`, который ждёт зелёного CI на этом же SHA, потом пушит снапшот репозитория в Space. `smoke.yml` после деплоя ждёт, пока `/api/health.revision` совпадёт с выкаченным SHA, и гоняет `smoke_prod.py`. Коммиты, меняющие только state-файлы `data/*.json`, CI и деплой не запускают (`paths-ignore`).

**Запрос пользователя.** Сайт `POST /api/predict` или бот → `predict_gate` → (кэш? → ответ) → rate-limit → слот → `predict_from_url` → ответ; параллельно `usage.record` и, если включён, `prediction_log`.

## Ключевые решения и их причины

| Решение | Почему | Где подробнее |
|---|---|---|
| База в GitHub Release, не в git и не на диске Space | Лимит 100 МБ на файл; ежедневный коммит раздувал бы историю; диск Space стирается | `db_release.py`, аудит 07-03 |
| State-файлы бота в репозитории через Contents API, зашифрованы Fernet | Единственное бесплатное персистентное хранилище; репо публичный, chat_id — PII | `subscriptions.py` |
| Два uvicorn-воркера, `OMP_NUM_THREADS=1` | HF free tier — 2 vCPU; один процесс упирался в GIL; 2×2 потока — оверсабскрипшн | `Dockerfile` |
| Один `predict_gate` для сайта и бота | Наплыв из телеграм-поста: тысяча человек проверяют один лот | `predict_gate.py`, CHANGELOG |
| Шардирование выдачи «район × комнаты» | Общая выдача обрезается на 1000 страницах (~20k из ~44k лотов), шарды покрывают почти весь город и делают delist честным | `config.py`, DATA.md |
| Режим прохода по состоянию базы, а не по флагу в воркфлоу | 12 дней докачка упиралась в потолок 1000/день и выглядела как успех | `pass_plan.py`, issue #152 |
| Временно́й сплит + purge, а не случайный | Случайный сплит смешивает время, метрики оптимистичны | `train.py`, issue #104/#153 |
| Метрика с ДИ, флагами репрезентативности и временно́й валидности | Одно число MAPE на нерепрезентативном тесте ничего не значит | `validity.py`, issue #158 |
| Runtime-lock без plotting-хвоста catboost, пин базового образа по digest | Образ на 100+ МБ легче; плавающий `3.11-slim` менял патч без причины | `scripts/gen_runtime_lock.py`, issue #119 |
| Фича-флаги `FEATURE_VISION`, `FEATURE_FORECAST` выключены по умолчанию | Вклад в качество не измерен; каждый показ — платный вызов Gemini | `config.py`, issue #157 |

## Внешние зависимости

| Сервис | Роль | Что случится, если недоступен |
|---|---|---|
| krisha.kz | Источник данных и детальные страницы для предикта | Рескрейп падает (`--fail-empty`), предикт по новой ссылке → ошибка; кэшированные ответы живут |
| Hugging Face Spaces | Хостинг сервиса | Сайт и бот недоступны; данные не теряются |
| GitHub (Actions, Releases, Contents API) | Конвейер, хранилище базы/моделей, state-файлы | Ночной проход не выполнится; Space при рестарте не скачает базу (если локальной нет) |
| Telegram Bot API | Бот, алерты, админ-отчёты | `tg_webhook` в health ≠ ok; keepalive шлёт алерт (если сам Telegram доступен раннеру) |
| Google Gemini | Текст объявления → параметры (бот), LLM-флаги, ремонт по фото | Без ключа всё деградирует мягко: только кэш, бот просит ссылку |
| OpenStreetMap Overpass | Разовые снапшоты POI и зон | Не влияет на рантайм: снапшоты в `models/` |
