# HTTP API

Базовый адрес прода: `https://dex719-krisha-fair-price.hf.space`. Все ответы JSON (кроме страниц и статики), кодировка UTF-8, тексты по-русски. OpenAPI — `/docs` (FastAPI), туда не попадают служебные ручки с `include_in_schema=False`.

## Публичные ручки

| Метод | Путь | Ответ | Кэш | Лимит |
|---|---|---|---|---|
| `POST` | `/api/predict` | `PredictResponse` | по id лота, `PREDICT_CACHE_TTL_S` = 600 с; негативный кэш битых ссылок 60 с | `RATE_LIMIT_PER_WINDOW` = 15 запросов на IP за `RATE_LIMIT_WINDOW_S` = 60 с; попадание в кэш лимит **не** тратит |
| `GET` | `/api/demo` | `{listing_id, url}` — случайный активный лот для кнопки «показать на примере» | пул лотов, `DEMO_POOL_TTL_S` | отдельный bucket `DEMO_RATE_LIMIT_PER_WINDOW` = 120 |
| `GET` | `/api/stats` | срез рынка: районы, ₸/м², распределения, недельный тренд | 600 с, single-flight, протухшее при сбое | — |
| `GET` | `/api/heatmap` | сетка ₸/м² для карты (ячейки ~400 м) | 600 с | — |
| `GET` | `/api/forecast` | прогноз ₸/м² на 13/26 недель по городу и районам | 600 с | — ; **404 «Прогноз отключён»**, пока `FEATURE_FORECAST` не включён |
| `GET` | `/api/health` | `HealthResponse` | свежесть данных — `HEALTH_CACHE_TTL_S` = 60 с; мета модели 300 с | — |
| `GET` | `/api/metrics` | телеметрия процесса: rps, коды, p50/p95/p99 по маршрутам, счётчики кэша и отказов | — | — |
| `GET`/`HEAD` | `/livez` | 200, процесс жив | — | — |
| `GET`/`HEAD` | `/readyz` | 200, когда модель загружена и база на месте; иначе 503 | — | — |
| `GET`/`HEAD` | `/`, `/stats`, `/about` | HTML | предсжатая статика из памяти, ETag от содержимого, `Cache-Control: no-cache` (= «спроси ETag») | — |
| `GET`/`HEAD` | `/robots.txt`, `/sitemap.xml` | текст/XML | — | — |
| `POST` | `/tg/webhook` | апдейты Telegram | — | защищён `X-Telegram-Bot-Api-Secret-Token`, иначе 403 |

Лимиты считаются **на процесс** и делятся на `WEB_CONCURRENCY`, чтобы суммарно по инстансу получалось объявленное число. IP берётся из `X-Forwarded-For` с учётом `TRUSTED_PROXY_HOPS` (дефолт 1 — прокси HF).

## `POST /api/predict`

Запрос: `{"url": "https://krisha.kz/a/show/1012607661"}` — только ссылки на объявления krisha.kz; `max_length=500` (реальный URL ~60 символов), остальное — 422.

Путь запроса: `predict_gate.peek` (готовый ответ → сразу, без лимита) → rate-limit → `predict_gate.cached_predict`: single-flight по id (параллельные запросы на тот же лот ждут один разбор), слот из `PREDICT_SLOTS` = 10 с ожиданием до `PREDICT_SLOT_WAIT_S` = 15 с → скачивание детальной страницы с бюджетом `USER_SCRAPE_TIMEOUT_S` = 5 с (3 с на коннект) → `predict_from_listing` → ответ. Параллельно `usage.record_event("predict")` и, если `PREDICTION_LOG=1`, запись в `data/prediction_log.json`.

Ответ `PredictResponse` (поля, которых может не быть, приходят `null`/пустым списком):

| Поле | Смысл |
|---|---|
| `listing_id`, `url`, `title`, `address`, `actual_price` | что за лот и его цена в объявлении, ₸ |
| `fair_price`, `fair_price_low`, `fair_price_high` | точечная оценка и интервал q10–q90 после CQR, ₸ |
| `verdict` | `GOOD_DEAL` / `FAIR` / `OVERPRICED` — по интервалу |
| `diff_pct` | отклонение цены объявления от `fair_price`, % |
| `top_factors[]` | `feature`, `impact` (вклад в log-цену), `impact_pct`, `impact_tenge`, `hint` (сравнение с медианой сегмента) |
| `details[]`, `complex_details[]`, `location_details[]` | пары `label/value` для карточки: характеристики, «О доме» из справочника ЖК, «Локация» (walk score, POI) |
| `price_history[]`, `days_on_market` | точки `price/observed_at` из нашей истории наблюдений и дни в выдаче |
| `liquidity` | `median_days`, `sample`, `scope` (`district_rooms`/`city`), `band` (`below/near/above`), `band_median_days`, `band_sample` — за сколько снятые аналоги уходили с рынка |
| `duplicate_of` | id другого лота с тем же fingerprint (перевыставление) |
| `analogs[]` | до N похожих активных лотов (kNN по площади, гео, году) с `ppsm` |
| `scam_risk` | `level` (`medium`/`high`), `below_pct`, `reasons[]` — цена ниже нижней границы интервала; уточняется признаками объявления |
| `renovation` | `level`, `label`, `comment` — только при `FEATURE_VISION=1` |
| `photos[]`, `description` | для карточки |

Коды ошибок: `422` — не ссылка на объявление / не удалось разобрать параметры; `429` — превышен лимит (`Retry-After`); `502 «Не удалось обработать объявление»` — krisha.kz не ответила или объявление снято (попадает в негативный кэш на 60 с); `503 «Сервис временно недоступен»` + `Retry-After` — все слоты заняты дольше `PREDICT_SLOT_WAIT_S` или модель не загружена.

## `GET /api/health`

```json
{
  "status": "ok", "model_loaded": true,
  "model_error_pct": 7.49, "model_median_error_pct": 5.73, "model_r2": 0.9193, "model_mae": 3893046,
  "model_error_ci_pct": [7.28, 7.72], "model_temporal_validity": false,
  "data_age_hours": 9.4, "freshness": "ok",
  "tg_webhook": "ok", "revision": "e4be840…"
}
```

- `freshness` = `stale`, если данные старше `DATA_STALE_AFTER_HOURS = 30` ч — значит ночной проход не долетел или Space не перезапустился на новой базе.
- `tg_webhook` ∈ `ok | unset | mismatch | no_token | no_public_url | error | unknown`; проверка не чаще раза в час и **самолечащая** — при `unset/mismatch` вебхук перерегистрируется. Из-за этого `/api/health` может отвечать до 10–15 с при недоступном api.telegram.org (типично на HF по IPv6) — таймауты HEALTHCHECK в Dockerfile выставлены с запасом.
- `revision` — SHA коммита, из которого собран образ (env `BUILD_REVISION` или `data/build_revision.txt`, который кладёт `deploy-hf.yml`; `null` локально); `smoke.yml` ждёт совпадения с выкаченным коммитом, прежде чем гонять смоук.

`scripts/health_check.py` (keepalive) считает состояние `up`, только если `status == ok`, `model_loaded == true`, `tg_webhook == ok`; иначе `degraded`; нет ответа — `down`. Сообщение в Telegram — только при смене состояния.

## Кэши и нагрузка

Расчётный сценарий: ссылку постят в большой телеграм-канал, тысяча человек за две минуты проверяют один лот.

- `api/cache.py::TTLCache` — TTL + per-key lock (стампед исключён: пересчёт делает один поток) + stale-while-error (если пересчёт упал — отдаётся последнее удачное значение вместо 503). Используется для health-свежести, меты модели, stats, heatmap, forecast, demo-пула.
- `api/static_cache.py` — HTML/CSS/JS/шрифты читаются и сжимаются один раз при старте (`mtime=0` → одинаковые байты и ETag у обоих воркеров), `If-None-Match` → 304, `Vary: Accept-Encoding`; шрифты и картинки — `immutable` на год. `GZipMiddleware` (`minimum_size=600`) остаётся для JSON.
- Каждый ответ несёт `X-Request-ID` — клиентский (буквы/цифры/`-_.`, ≤64) или сгенерированный; по нему искать в логах.
- Замер локально: `python scripts/loadtest.py <url>` (не трогает `/api/predict` — нельзя долбить krisha.kz синтетикой). Живой прод — `/api/metrics`. Ориентиры из CHANGELOG: `GET /` 706 rps без сжатия / 361 rps с сжатием на лету до `static_cache`, два воркера, 25 клиентов.

## Фронт (`static/`)

Vanilla JS, собственная дизайн-система `design.css` (светлая/тёмная тема, self-hosted шрифты), Leaflet для карты на `/stats`, GSAP + ScrollTrigger для анимаций. Страницы: `index.html` (оценка), `stats.html` (рынок), `about.html` (как считается), `404.html`. Фронт ходит только в ручки выше; deep-link'и в бота (`https://t.me/fairprice_kzbot?start=…`) — для «Следить за ценой» и подписки на район. Приёмочные тесты фронта — `tests/test_home_*.py`, `tests/test_about_page.py`, e2e в `tests/e2e/`.
