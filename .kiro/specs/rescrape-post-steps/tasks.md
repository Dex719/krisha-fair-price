# Tasks: rescrape-post-steps

Источник контекста — [bugfix.md](bugfix.md), [design.md](design.md). Отмечай
чекбоксы по мере выполнения и синхронизируй таблицу Progress внизу.

## Фаза 1 — Пакетная оценка

- [x] **TSK-001**: Общая формула цены/интервала/вердикта `_price_pool`
  - Requirement: FR-4
  - Deliverables: `src/krisha/predict.py`
  - Детали: вынести из `_predict_from_listing` расчёт `fair`/интервала/вердикта
    для N строк пула; карточка вызывает хелпер для одной строки.
  - Acceptance: существующие тесты `test_predict*.py` зелёные без правок.
  - Факт: хелпер вынесен, карточка зовёт его для одной строки; `test_predict.py` — зелёный без правок.

- [x] **TSK-002**: `predict_listings_batch` + `log_predictions`
  - Requirement: FR-3, FR-4, FR-5
  - Deliverables: `src/krisha/predict.py`, `src/krisha/db.py`, `src/krisha/features.py` (`listings_to_frame`)
  - Acceptance: AC-4.1 — совпадение с карточкой на реальной модели.
  - Факт: `test_batch_matches_the_card` и `test_batch_rows_do_not_depend_on_their_neighbours` — зелёные на модели из `models/`.

- [x] **TSK-003**: `find_good_deals` на пакете
  - Requirement: FR-3, FR-5
  - Deliverables: `src/krisha/alerts.py`
  - Детали: порции по 500, одна транзакция на порцию, фолбэк порции по одному лоту.
  - Acceptance: AC-3.1, AC-5.1; дедуп и сортировка не изменились.
  - Факт: карточка не вызывается (тест с заглушкой-«капканом»), в `predictions` — строка на лот; в лог — итоговая строка с временем.

- [x] **TSK-004**: Тесты пакета
  - Requirement: AC-3.1, AC-4.1, AC-5.1
  - Deliverables: `tests/test_alerts_batch.py`
  - Факт: 6 тестов зелёные (WSL, Python 3.14).

## Фаза 2 — Воркфлоу

- [x] **TSK-005**: Критический путь первым, таймаут и повторная заливка
  - Requirement: FR-1, FR-2, FR-6, NFR-2
  - Deliverables: `.github/workflows/rescrape.yml`
  - Acceptance: AC-1.1.
  - Факт: порядок Upload → Restart → Snapshot → Alerts (`timeout-minutes: 15`, `id: alerts`) → «Upload DB again» при двух успехах.

- [x] **TSK-006**: Тест порядка шагов
  - Requirement: AC-1.1
  - Deliverables: `tests/test_rescrape_workflow.py`
  - Факт: 3 теста зелёные; бюджет прохода читается из самого YAML.

## Фаза 3 — Снапшот

- [x] **TSK-007**: Ретрай и публикация черновика
  - Requirement: FR-7
  - Deliverables: `scripts/publish_snapshot.py`, `tests/test_publish_snapshot.py`
  - Acceptance: AC-7.1, AC-7.2.
  - Факт: 3 новых теста на фейковом `gh` (500 на create → вторая попытка публикует; черновик дописывается и публикуется; исчерпание → exit 1) — зелёные.

## Фаза 4 — Проверка

- [x] **TSK-008**: Регрессии §5 и замер NFR-1
  - Deliverables: прогон `pytest`, `ruff`, замер `find_good_deals` на свежей базе
  - Acceptance: всё зелёное (кроме известного красного локальной среды), Alerts ≤ 5 мин.
  - Факт: `pytest` 669 passed / 1 failed — известное красное локальной среды
    (`test_static_precompress`, Python 3.14 + starlette 1.3; на чистом main то же), было 642 + 27 новых;
    `ruff check src tests scripts` — чисто. Замер на `db-latest` 2026-09-23 (окно 3016 лотов):
    старый путь 0.93 с/лот ≈ 47 мин на окно, новый `find_good_deals` — **1.3 с**;
    эквивалентность пакет/карточка на 60 реальных лотах — 300/300 полей.
    Смержено в c5c42ec (PR #194, 2026-09-23). Остаётся: первый ночной rescrape
    2026-09-24 — порядок шагов и время Alerts в логе, утренний смоук `freshness: ok`.

## Dependency graph

```
TSK-001 ──► TSK-002 ──► TSK-003 ──► TSK-004 ──┐
TSK-005 ──► TSK-006 ──────────────────────────┼──► TSK-008
TSK-007 ──────────────────────────────────────┘
```

## Progress

| Задача | Статус |
|---|---|
| TSK-001 общая формула | Complete |
| TSK-002 пакет + лог | Complete |
| TSK-003 find_good_deals | Complete |
| TSK-004 тесты пакета | Complete (6/6) |
| TSK-005 воркфлоу | Complete |
| TSK-006 тест воркфлоу | Complete (3/3) |
| TSK-007 снапшот | Complete (3 новых теста) |
| TSK-008 регрессии и замер | Complete локально (Alerts 47 мин → 1.3 с); ночной прогон — после мержа |
