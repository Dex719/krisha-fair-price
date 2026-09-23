# Design: predict-edge-listings

Источник — [bugfix.md](bugfix.md).

## Компоненты

| Где | Что меняется |
|---|---|
| `krisha.features.build_features` | `log_price = log1p(pd.to_numeric(price, errors="coerce"))` — как у остальных числовых колонок |
| `krisha.predict` | `class ListingNotFound(RuntimeError)`; `predict_from_url`: `html is None` → `ListingNotFound` (на пользовательском пути `None` = 404) |
| `krisha.api.app` `/api/predict` | `except ListingNotFound` → 404 + detail; стоит выше `except RuntimeError` |
| `krisha.bot` | ветка `ListingNotFound` с понятным текстом; `except Exception` в конце — человеку всегда ответ; `/track` на 404 — `ListingNotFound` |
| `static/index.html` | 404 показывает `detail`, как 503 |
| `scripts/smoke_prod.py` | 404 на предикт → новый демо-лот, до `DEMO_REPICKS = 2` раз |

## Решения

### D1. `ListingNotFound` — подкласс `RuntimeError`
Всё, что уже ловит `RuntimeError` (в т.ч. `/track`, сторонние вызовы), продолжит его
обрабатывать; новые ветки в API и боте дают точный код и текст. В негативный кэш калитки
он попадает по умолчанию (`except Exception`) — и это нужно: снятое объявление не
должно гонять скрейп на каждый запрос.

### D2. 404, а не 410
410 Gone точнее семантически, но фронт, смоук и клиенты привыкли к 404 как «такого
нет»; detail объясняет причину. Лишней сущности не заводим.

### D3. Смоук берёт другой демо-лот на 404, а не «терпит» 404
Смоук обязан проверять предикт; снятый лот не даёт это сделать, но и не говорит о
поломке сервиса. Новый лот — честная проверка; после исчерпания — падение с текстом.

## Тесты

| Что | Где |
|---|---|
| AC-1.1, AC-1.2 | `tests/test_predict_edge_listings.py` (реальная модель + фикстура) |
| AC-2.1, AC-2.2 | там же + `TestClient` |
| AC-3.1 | `tests/e2e/test_home_e2e.py` (в CI) |
| AC-4.1 | `tests/test_bot.py` |
| AC-5.1 | `tests/test_smoke_prod.py` |
