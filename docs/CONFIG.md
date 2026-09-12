# Конфигурация

Два уровня: **переменные окружения** (меняются перезапуском Space или секретами Actions, без пересборки) и **константы `src/krisha/config.py`** (меняются коммитом). Правило проекта: всё, что можно подкрутить, — в `config.py`; всё, что зависит от среды или является секретом, — в env. Фича-флаги читаются функцией в момент вызова, а не при импорте, чтобы тесты подменяли их `monkeypatch`.

## Переменные окружения

### Сервис (Space)

| Переменная | Дефолт | Назначение |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Токен бота. Без него бот выключен, сайт работает. Также ключ шифрования state-файлов, если нет `STATE_ENCRYPTION_KEY` |
| `STATE_ENCRYPTION_KEY` | — | Секрет для Fernet-шифрования `subscriptions.json`/`tracked.json`. Задать явно, чтобы смена токена не теряла подписки |
| `GITHUB_PAT` | — | Fine-grained PAT с `contents:write` на репозиторий — коммиты state-файлов из Space. Без него состояние живёт до деплоя |
| `GITHUB_REPO` | `Dex719/krisha-fair-price` | Куда коммитить state и откуда качать релизы |
| `PUBLIC_BASE_URL` | — | Публичный адрес для webhook и deep-link'ов; иначе `RAILWAY_PUBLIC_DOMAIN` / `SPACE_HOST` (HF выставляет сам) |
| `TG_API_BASE` | `https://api.telegram.org` | Прокси Bot API (`docs/tg-proxy-worker.js`), если с хостинга Telegram недоступен |
| `TG_ADMIN_CHAT_ID` | — | Личный чат владельца: админ-отчёты, алерты о банах/деградации |
| `GEMINI_API_KEY` | — | Gemini: текст → параметры (бот), LLM-флаги, ремонт по фото. Без ключа — мягкая деградация до кэша |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Модель Gemini |
| `FEATURE_VISION` | `0` | Оценка ремонта по фото на предикте (платный вызов на каждый показ) |
| `FEATURE_FORECAST` | `0` | `/api/forecast` и блок прогноза на `/stats` |
| `PREDICTION_LOG` | `auto` (= выкл.) | `1` — писать предикты в `data/prediction_log.json`. В Actions игнорируется (всегда выкл.) |
| `WEB_CONCURRENCY` | `2` (Dockerfile) | Число uvicorn-воркеров; лимиты делятся на него |
| `OMP_NUM_THREADS` и др. `*_NUM_THREADS` | `1` (Dockerfile) | Потоки native-пулов: 2 воркера × 1 поток на 2 vCPU |
| `KRISHA_DB_AUTO` | `1` | `0` — не скачивать базу из релиза при старте |
| `KRISHA_MODEL_AUTO` | `1` | `0` — не скачивать модели из `model-latest` |
| `KRISHA_DB_URL`, `KRISHA_MODEL_URL` | URL релизов `db-latest` / `model-latest` | Переопределение источников артефактов |
| `BUILD_REVISION` | из `data/build_revision.txt` | SHA деплоя для `/api/health.revision` |
| `TRUSTED_PROXY_HOPS` | `1` | Сколько прокси доверять в `X-Forwarded-For` при определении IP для лимитов |
| `RATE_LIMIT_PER_WINDOW` / `RATE_LIMIT_WINDOW_S` | `15` / `60` | Лимит `/api/predict` на IP за окно (на инстанс) |
| `DEMO_RATE_LIMIT_PER_WINDOW` | `120` | Отдельный лимит `/api/demo` (CGNAT мобильных операторов) |
| `DEMO_POOL_TTL_S` | `600` | Время жизни пула демо-лотов |
| `HEALTH_CACHE_TTL_S` | `60` | Как часто пересчитывать свежесть данных в `/api/health` |
| `PREDICT_CACHE_TTL_S` | `600` | Кэш готового разбора по id лота |
| `PREDICT_NEGATIVE_TTL_S` | `60` | Негативный кэш битых/снятых ссылок |
| `PREDICT_SLOTS` | `10` | Одновременных походов на krisha.kz на процесс |
| `PREDICT_SLOT_WAIT_S` | `15` | Сколько ждать слот до 503 + `Retry-After` |
| `PREDICT_WAIT_S` | `20` | Общий потолок ожидания ответа `/api/predict` на уровне ручки (`anyio.move_on_after`) — истёк → 503 + `Retry-After` |
| `USER_SCRAPE_TIMEOUT_S` / `USER_SCRAPE_CONNECT_S` | `5` / `3` | Сетевой бюджет пользовательского предикта (краулер живёт с 30 с) |
| `USAGE_FLUSH_SYNC` | — | `1` — флашить статистику использования синхронно (тесты, Actions) |
| `STATE_FORCE_OVERWRITE` | — | `1` — писать state-файлы без слияния с удалённой версией. Только ручной ремонт |

### Сбор и конвейер (GitHub Actions)

| Переменная / секрет | Где | Назначение |
|---|---|---|
| `secrets.GITHUB_TOKEN` | все воркфлоу | Релизы, коммиты моделей, state-файлы из `send_alerts` (fallback вместо `GITHUB_PAT`) |
| `secrets.HF_TOKEN` | `rescrape`, `retrain`→`deploy-hf`, `space-keepalive` | Push снапшота в Space и его рестарт. Без него рестарт после рескрейпа пропускается — Space поднимет новую базу только при следующем деплое |
| `secrets.TELEGRAM_BOT_TOKEN`, `secrets.TG_ADMIN_CHAT_ID` | `rescrape`, `retrain`, `smoke`, `space-keepalive` | Алерты и отчёты |
| `secrets.TG_CHANNEL_ID` | `rescrape` | Канал для топ-5 (`@имя` или `-100…`) |
| `vars.PROD_BASE_URL` | `smoke` | Адрес прода для смоука (дефолт — HF Space) |
| `KRISHA_DELAY_MIN` / `KRISHA_DELAY_MAX` | `rescrape` (инпуты) | Паузы между запросами; пусто = пресет режима. Пустая строка читается как «не задано» (`_env_float`) |
| `HEALTH_URL` | `space-keepalive` | Что опрашивать (`/api/health`) |
| `SMOKE_TIMEOUT_S` | `smoke` | Таймаут запросов смоука |
| `LLM_BATCH_DELAY` | `scripts/analyze_flags.py` | Пауза между вызовами Gemini в пакетном анализе (дефолт 7 с ≈ 8 req/мин, бесплатный тариф) |
| `GITHUB_ACTIONS` | ставит GitHub | По нему `prediction_log` понимает, что он в Actions, и не пишет |

Секрет `GEMINI_API_KEY` в воркфлоу **не** передаётся: пакетный LLM-анализ — ручной запуск.

## Константы `config.py`, которые чаще всего трогают

| Константа | Значение | Смысл |
|---|---|---|
| `REQUEST_DELAY_RANGE` | (2.0, 4.0) с | Дефолтные паузы `PoliteClient` вне режимов прохода |
| `REQUEST_TIMEOUT`, `MAX_RETRIES` | 30 с, 3 | Сетевой бюджет краулера |
| `ALMATY_DISTRICT_SLUGS`, `ROOM_SHARDS` | 8 × 4 | Шарды выдачи. `Наурызбайский` — слаг с `-iy`, с `-ij` не существует |
| `DISTRICT_RU` | 8 записей | Транслит krisha → русское имя района; каноничная копия здесь, остальные модули реэкспортируют |
| `PRICE_MIN/MAX`, `AREA_MIN/MAX`, `PPSM_MIN/MAX` | 5 млн–1.5 млрд ₸; 10–500 м²; 100k–5M ₸/м² | Data-contract продажи; отклонённое → `parse_anomalies` |
| `RENT_PRICE_MIN/MAX` | 20k–10M ₸/мес | Контракт аренды (продажный отбраковывал всю аренду) |
| `ALMATY_BBOX` | 42.95–43.50 × 76.55–77.25 | Координаты вне — чужой город или битый парсинг |
| `ALMATY_CENTER` | (43.2398, 76.8898) | Для `dist_center_km` |
| `SHARED_PIN_MIN` | 5 | Сколько лотов на одной точке = метка ЖК, не квартира (`coords_approx`) |
| `STALE_DELISTED_DAYS` | 90 | Сколько дней цене снятого лота доверяем в train |
| `MAX_TRUSTED_DELIST_LAG_DAYS` | 7 | Порог доверия к снятию — общий для алертов `/track` и ликвидности, нарочно один |
| `RANDOM_STATE` | 42 | Сиды CatBoost/сплитов |

Константы прохода — в `scraping/rescrape.py` (`DELIST_AFTER_DAYS = 3`, `RECOVERY_GAP_DAYS = 2.5`) и `scraping/pass_plan.py` (`DRAIN_MODE`, `STEADY_MODE`, `DRAIN_ENTER_BACKLOG = 5000`, `DRAIN_EXIT_BACKLOG = 3000`); константы обучения — в `train.py` (см. [MODEL.md](MODEL.md)); порог вердикта `VERDICT_THRESHOLD = 0.10` — в `predict.py`.

## Что нужно, чтобы поднять свой экземпляр

Минимум для сайта: ничего — Space скачает базу и модели из публичных релизов. Для бота — `TELEGRAM_BOT_TOKEN` (+ публичный адрес, который HF даёт сам). Чтобы подписки переживали деплой — `GITHUB_PAT` и `STATE_ENCRYPTION_KEY`. Для Gemini-функций — `GEMINI_API_KEY`. Для конвейера в форке — секреты `HF_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TG_ADMIN_CHAT_ID` и правка имени Space в `deploy-hf.yml` / `space-keepalive.yml` (захардкожен `Dex719/krisha-fair-price`).
