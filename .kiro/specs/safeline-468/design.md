# Design: safeline-468 — переоткрытие 2026-09-23

Источник требований — [bugfix.md](bugfix.md), раздел §7 (FR-7…FR-11). Фаза 1–4
(исходный фикс) проектировалась прямо в bugfix.md/tasks.md; этот файл описывает
только переоткрытую часть.

## Overview

Три слоя, по одному на найденную причину:

1. **Липкая сессия пользовательского пути** — куки удачной сессии живут на процесс
   и засевают каждый новый пользовательский клиент. Попав однажды в ведро A,
   процесс в нём и остаётся, как долгоживущий клиент краулера.
2. **Честная классификация отказа** — SafeLine узнаётся по маркерам при любом коде;
   любой исход «страницы нет и это не 404» на пользовательском пути — отказ
   источника (`SourceUnavailable` → 503), а не внутренняя ошибка (502).
3. **Видимость** — исходы попыток суммируются в `/api/metrics` (`scrape_*`),
   смоук печатает их в тексте падения.

## Components and interfaces

### `krisha.scraping.client`

```python
class SourceUnavailable(RuntimeError): ...        # источник не отдал страницу
class ChallengeBlocked(SourceUnavailable): ...    # ...и это SafeLine (челлендж/блок)

class StickyCookies:
    """Куки последней удачной пользовательской сессии, общие на процесс."""
    def cookies(self) -> httpx.Cookies        # копия для нового клиента
    def remember(self, jar: httpx.Cookies)    # после 200 со страницей
    def forget(self)                          # после страницы SafeLine

class PoliteClient:
    def __init__(..., raise_on_challenge=False, cookie_store: StickyCookies | None = None)
```

Классификация ответа в `get` (порядок веток):

| Ответ | Ветка | Счётчики | Сессия |
|---|---|---|---|
| 468, или не-403 с маркерами SafeLine | челлендж | `http_468` | `forget` + сброс, пауза `challenge_wait_s` |
| 200 без маркеров | успех | `http_200` | `remember` |
| 404 | конец, `None` | `http_404` | — |
| 429 | троттлинг (как было) | `http_429` | — |
| 403 с маркерами | блок WAF, семантика 403 | `http_403`; `waf_block` — отдельным полем (подвид 403: сумма `counters` = число запросов для `fit_detail_caps`) | `forget` + сброс |
| 403 без маркеров | как было | `http_403` | — |
| прочие коды / сетевые ошибки | как было | `http_other` / `errors` | — |

Исчерпание попыток: с `raise_on_challenge=True` (пользовательский путь) — если был
челлендж или блок WAF, `ChallengeBlocked`, иначе `SourceUnavailable`. С `False`
(краулер) — прежняя логика: серия банов по 403, затем `None`.

Хук исходов: `on_attempt: Callable[[str], None] | None` — вызывается с именем
счётчика на каждую попытку (`"http_200"`, `"waf_block"`, …). Краулер его не
передаёт.

### `krisha.predict`

- Модульное `USER_COOKIES = StickyCookies()` — одно на процесс (воркер uvicorn).
- `predict_from_url(url, live_vision, timeout, on_attempt=None)` передаёт
  `cookie_store=USER_COOKIES` и `on_attempt` в `PoliteClient`.

### `krisha.predict_gate`

- Не кэширует `SourceUnavailable` (вместо `ChallengeBlocked`; подкласс покрыт).
- `_run` передаёт `on_attempt=_count_attempt` → `metrics.bump(f"scrape_{name}")`.

### `krisha.api.app` / `krisha.bot`

- `except ChallengeBlocked` → `predict_challenge` (как было); новая ветка
  `except SourceUnavailable` → `predict_source_unavailable`; обе — 503 +
  `Retry-After: 5` + тот же detail «Источник временно не отдаёт объявление»
  (фронт показывает detail любого 503 — правка фронта не нужна).
- Бот: ловит `SourceUnavailable` (базовый класс) тем же текстом; `/track` получает
  то же хранилище кук.

### `scripts/smoke_prod.py`

При падении проверки `POST /api/predict` смоук делает один `GET /api/metrics` и
добавляет в текст ошибки `scrape_*`-счётчики (fail-soft: метрики недоступны —
падение с исходным текстом).

## Решения

### D4. Липкие куки на процесс, а не общий долгоживущий `PoliteClient`
- **Варианты:** (1) один общий `PoliteClient` на процесс; (2) общее хранилище кук,
  клиент по-прежнему на запрос.
- **Решение:** (2). `PoliteClient` не рассчитан на долгую жизнь в вебе
  (`_latencies` растёт без границы, счётчики и `_ban_streak` — на проход) и на
  конкурентный доступ; хранилище кук — маленький объект под локом, клиенты
  остаются изолированными, счётчики — на запрос.

### D5. `ChallengeBlocked` — подкласс нового `SourceUnavailable`
- **Решение:** базовый класс для любого отказа источника, `ChallengeBlocked`
  сохраняет имя и смысл (SafeLine) и существующие ветки/метрику
  `predict_challenge`; вызывающие ловят базовый класс там, где причина не важна.

### D6. 403 со страницей SafeLine оставляет семантику 403 для краулера
- **Решение:** блок WAF по IP сменой сессии не лечится, а серия 403 — законный
  сигнал краулеру остановить проход (issue #101). Меняем только классификацию
  (`waf_block`) и то, что такая сессия не передаётся дальше.

## Error handling и бюджет

Худший случай пользовательского пути не меняется: 3 попытки × (≤1 с паузы +
таймаут 5 с) + паузы между попытками ≤ 2 × 2 с — в пределах `PREDICT_WAIT_S`
(20 с), как и в §2 NFR-2. С липкой сессией обычный случай — одна попытка.

## Тестовая стратегия

| Что | Где |
|---|---|
| Липкие куки: засев, `forget` на 468, `remember` на 200 (AC-7.1, AC-7.2) | `tests/test_scraping_client.py` |
| 403 с маркерами: `waf_block`, `BanDetected` краулеру (AC-8.1) | `tests/test_scraping_client.py` |
| Смешанные исходы → `ChallengeBlocked`/`SourceUnavailable` (AC-9.1, AC-9.2) | `tests/test_scraping_client.py` |
| API 503 + метрика + негативный кэш (AC-9.3), `scrape_*` (AC-10.1) | `tests/test_predict_gate.py` |
| Счётчики в тексте падения смоука (AC-11.1) | `tests/test_smoke_prod.py` |
