# Tasks: safeline-468

Источник контекста — [bugfix.md](bugfix.md). Отмечай чекбоксы по мере выполнения
и синхронизируй таблицу Progress внизу.

## Фаза 1 — Транспорт: распознать челлендж и ретраить со свежей сессией

- [x] **TSK-001**: Класс `ChallengeBlocked` и детект SafeLine в `PoliteClient`
  - Requirement: FR-1, AC-1.2, AC-3.1
  - Deliverables: `src/krisha/scraping/client.py`
  - Детали: `CHALLENGE_STATUSES = frozenset({468})`; `_looks_like_challenge(text)`
    по маркерам `/.safeline/` и `slg-title` (ловит челлендж, пришедший с 200);
    `class ChallengeBlocked(RuntimeError)` рядом с `BanDetected`.
  - Acceptance: 468 и «200 со страницей SafeLine» ведут в одну ветку; 403/429 не задеты.

- [x] **TSK-002**: Пересоздание HTTP-сессии между попытками
  - Requirement: FR-1, AC-1.3
  - Deliverables: `src/krisha/scraping/client.py` (`_new_session()`, `_reset_session()`)
  - Детали: вынести конструирование `httpx.Client` в метод; на челлендже закрыть
    текущий клиент и создать новый (чистый cookie jar, новые соединения), затем
    короткая пауза `challenge_wait_s` (дефолт 1.0 с — свежая сессия проходит сразу,
    длинный бэкофф здесь только жжёт бюджет).
  - Acceptance: AC-1.3 — объект `_client` после челленджа другой; `close()`/`__exit__`
    закрывают актуальный клиент без утечки предыдущих.

- [x] **TSK-003**: Счётчик `http_468` и исчерпание попыток
  - Requirement: FR-4, AC-1.1, AC-1.2
  - Deliverables: `src/krisha/scraping/client.py`
  - Детали: `counters["http_468"]`; если ВСЕ попытки — челлендж, поднять
    `ChallengeBlocked` (а не вернуть `None`, чтобы вызывающий отличил причину);
    счётчик попадает в `stats` → summary-JSON прохода.
  - Acceptance: AC-1.1, AC-1.2.

- [x] **TSK-004**: Тесты транспорта
  - Requirement: AC-1.1, AC-1.2, AC-1.3, AC-3.1
  - Deliverables: `tests/test_scraping_client.py`
  - Детали: в стиле существующих `_FakeResponse`; кейсы — «468 → 468 → 200»,
    «все 468 → ChallengeBlocked», «сессия пересоздана», «200 с телом SafeLine»,
    плюс регрессия: 403/429 ведут себя как раньше.
  - Acceptance: новые тесты зелёные, старые не менялись.

## Фаза 2 — Пользовательский путь: бюджет, статус, телеметрия

- [x] **TSK-005**: Пробросить `ChallengeBlocked` через predict
  - Requirement: FR-2, FR-3
  - Deliverables: `src/krisha/predict.py`
  - Детали: `predict_from_url` — `max_retries=2 → 3`, `challenge_wait_s` короткий;
    `ChallengeBlocked` не глотать (сейчас любой `RuntimeError` уходит в 502).
    Проверить бюджет: 3 × (0.5–1.0 пауза + ~2 с) + 2 × 1.0 ≈ 11 с < `PREDICT_WAIT_S` 20 с.
  - Acceptance: NFR-2 — worst case укладывается в 20 с.

- [x] **TSK-005b**: Не кэшировать челлендж в негативном кэше
  - Requirement: FR-3 (следствие), найдено по ходу реализации
  - Deliverables: `src/krisha/predict_gate.py`
  - Детали: `ChallengeBlocked` — в один `except` с `PredictBusy`/`InvalidListingUrl`.
    Челлендж про состояние ЧУЖОГО сервера, а не про объявление; закэшировать
    его на 60 с значило бы в главном сценарии проекта (тысяча человек с одной
    ссылкой из поста) запереть всех из-за невезения первого.
  - Acceptance: повторный запрос по тому же URL идёт наружу заново.

- [x] **TSK-005c**: Флаг `raise_on_challenge` — не ронять краулер
  - Requirement: Unchanged behavior, найдено по ходу реализации
  - Deliverables: `src/krisha/scraping/client.py`, `src/krisha/predict.py`, `src/krisha/bot.py`
  - Детали: `ChallengeBlocked` — подкласс `RuntimeError`, а краулер/sweep ловят
    только `BanDetected`; без флага новое исключение уронило бы ночной проход
    трейсбеком. По умолчанию `get` возвращает None, как раньше; флаг включают
    веб и бот, которым нужно отличить 503 от 502.
  - Acceptance: тест «краулерный дефолт возвращает None, сессия всё равно меняется».

- [x] **TSK-006**: `/api/predict` отвечает 503 на исчерпанный челлендж
  - Requirement: FR-3, FR-4, AC-2.1
  - Deliverables: `src/krisha/api/app.py`
  - Детали: ветка `except ChallengeBlocked` ПЕРЕД `except RuntimeError`;
    `metrics.bump("predict_challenge")`; 503 + `Retry-After` + detail
    «Источник временно не отдаёт объявление, попробуйте ещё раз».
    Порядок except важен: `ChallengeBlocked` — подкласс `RuntimeError`.
  - Acceptance: AC-2.1; 502 остаётся только для настоящих внутренних сбоев.

- [x] **TSK-007**: Бот — тот же путь
  - Requirement: FR-3
  - Deliverables: `src/krisha/bot.py`
  - Детали: проверить обработку ошибок предиката в боте; текст про внешний
    источник вместо общей ошибки.
  - Acceptance: пользователь бота видит осмысленную причину.

- [x] **TSK-008**: Тесты API
  - Requirement: AC-2.1
  - Deliverables: `tests/test_predict.py`, `tests/test_api_schema.py` (или `test_api_load_hardening.py`)
  - Acceptance: `ChallengeBlocked` → 503 + `Retry-After`; счётчик в `/api/metrics`.

## Фаза 3 — Фронт и смоук

- [x] **TSK-009**: Текст на фронте для 503-челленджа
  - Requirement: FR-5
  - Deliverables: `static/index.html` (блок обработки `!res.ok`, ~строка 784)
  - Детали: отличить 503 с этим detail от «сервис не отвечает».
  - Acceptance: пользователь не видит обвинение нашего сервиса во внешнем отказе.

- [x] **TSK-010**: Смоук с ретраем и честной диагностикой
  - Requirement: FR-6, AC-4.1
  - Deliverables: `scripts/smoke_prod.py`, `tests/test_smoke_prod.py`
  - Детали: до 3 попыток `POST /api/predict` (при 503-челлендже — с паузой);
    при исчерпании — падение с текстом, явно называющим SafeLine/внешний
    источник. Смоук остаётся строгим: молча зеленеть нельзя (issue #154),
    но и путать внешний отказ с поломкой сервиса — тоже.
  - Acceptance: AC-4.1; тест на «два челленджа, третий успех → зелёный».

## Фаза 4 — Проверка

- [x] **TSK-011**: Регрессии по списку §5 bugfix.md
  - Deliverables: прогон pytest + ручной прогон `Prod smoke` (workflow_dispatch) после деплоя
  - Acceptance: все пункты §5 отмечены с evidence.

## Фаза 5 — Переоткрыто 2026-09-23: 502 на проде (bugfix.md §7, design.md)

- [x] **TSK-012**: `SourceUnavailable`, SafeLine при любом коде, `waf_block`, исчерпание
  - Requirement: FR-8, FR-9, AC-8.1, AC-9.1, AC-9.2
  - Deliverables: `src/krisha/scraping/client.py`
  - Acceptance: смешанные исходы пользовательского пути → 503-класс; краулер без изменений.
  - Факт: «468, таймаут, 468» → `ChallengeBlocked`; 403 без маркеров → `SourceUnavailable` (краулеру — `None`); 403 SafeLine → `waf_block` + `BanDetected` краулеру.

- [x] **TSK-013**: Липкие куки `StickyCookies` + хук исходов `on_attempt`
  - Requirement: FR-7, FR-10, AC-7.1, AC-7.2
  - Deliverables: `src/krisha/scraping/client.py`, `src/krisha/predict.py`
  - Факт: тесты на настоящем `httpx` с `MockTransport`; живой A/B с KZ IP на 8 лотах подряд —
    липкие куки: 8 попыток, 0 × 468, 0 отказов; свежая сессия (как было): 16 попыток, 11 × 468, 3 отказа из 8.

- [x] **TSK-014**: Калитка, API, бот
  - Requirement: FR-9, FR-10, AC-9.3, AC-10.1
  - Deliverables: `src/krisha/predict_gate.py`, `src/krisha/api/app.py`, `src/krisha/bot.py`
  - Факт: 503 + `predict_source_unavailable`, без негативного кэша; `scrape_*` в `/api/metrics`;
    бот и `/track` — на липких куках и счётчиках. Заглушки `predict_from_url` в тестах
    (21 шт.) получили параметр `on_attempt`.

- [x] **TSK-015**: Смоук печатает `scrape_*` при падении
  - Requirement: FR-11, AC-11.1
  - Deliverables: `scripts/smoke_prod.py`
  - Факт: при падении предикта — один `GET /api/metrics`, в тексте `scrape_*`/`predict_*`; метрики недоступны — исходный текст.

- [x] **TSK-016**: Тесты фазы 5
  - Deliverables: `tests/test_scraping_client.py`, `tests/test_predict_gate.py`, `tests/test_smoke_prod.py`, `tests/test_bot.py`
  - Факт: +9 клиента, +3 калитки, +2 смоука, +1 бота — зелёные; затронутые наборы 147/147.

- [x] **TSK-017**: Регрессии §5 + §7 и проверка после выката
  - Acceptance: `pytest` зелёный; после деплоя — `Prod smoke` зелёный,
    `/api/metrics` показывает `scrape_*` с IP Hugging Face.
  - Факт (выкат c5c42ec, PR #194, 2026-09-23): Prod smoke после деплоя зелёный;
    первый предикт на проде — `scrape_http_403: 1, scrape_waf_block: 1, scrape_http_200: 1`:
    IP Hugging Face получает **403-блок SafeLine**, а не 468 (гипотеза §7 подтверждена),
    смена сессии проходит со второй попытки. Те же 5 лотов, что утром дали 3 × 502 за 9–13 с,
    после выката — 5 × 200 за 2.5–3.8 с, все с одной попытки (липкие куки держат ведро A).

## Dependency graph

```
TSK-001 ──► TSK-002 ──► TSK-003 ──► TSK-004
                            │
                            ├──► TSK-005 ──► TSK-006 ──► TSK-008
                            │                   └──► TSK-007
                            │                   └──► TSK-009
                            └──► TSK-010
TSK-004, TSK-008, TSK-010 ──► TSK-011
TSK-012 ──► TSK-013 ──► TSK-014 ──► TSK-016 ──► TSK-017
                  └──► TSK-015 ──────┘
```

## Progress

| Задача | Статус |
|---|---|
| TSK-001 детект челленджа | Complete |
| TSK-002 сброс сессии | Complete |
| TSK-003 счётчик + ChallengeBlocked | Complete |
| TSK-004 тесты транспорта | Complete (12/12 зелёные) |
| TSK-005 predict: бюджет и проброс | Complete |
| TSK-005b негативный кэш | Complete |
| TSK-005c raise_on_challenge | Complete |
| TSK-006 API 503 + метрика | Complete |
| TSK-007 бот | Complete |
| TSK-008 тесты API | Complete (14/14) |
| TSK-009 фронт | Complete |
| TSK-010 смоук | Complete (8/8 зелёные) |
| TSK-011 регрессии | Локально Complete; **прод-смоук не позеленел** (4 × 502 из 14 после выката) → переоткрыто, фаза 5 |
| TSK-012 классификация отказа | Complete |
| TSK-013 липкие куки + хук | Complete (живой A/B: 0 × 468 против 11) |
| TSK-014 калитка/API/бот | Complete |
| TSK-015 смоук | Complete |
| TSK-016 тесты фазы 5 | Complete (15 новых) |
| TSK-017 регрессии + прод | Complete (прод: 403-блок SafeLine подтверждён, 5/5 → 200) |
