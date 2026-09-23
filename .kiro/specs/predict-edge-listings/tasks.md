# Tasks: predict-edge-listings

Источник — [bugfix.md](bugfix.md), [design.md](design.md).

- [x] **TSK-001**: `build_features` без цены — Requirement: FR-1, NFR-1; Deliverables: `src/krisha/features.py`
  - Факт: `pd.to_numeric` перед `log1p`; AC-1.1/AC-1.2 — `tests/test_predict_edge_listings.py`, реальная модель
- [x] **TSK-002**: `ListingNotFound` в `predict_from_url` — Requirement: FR-2; Deliverables: `src/krisha/predict.py`
  - Факт: `ListingNotFound(RuntimeError)`; AC-2.1 — настоящий `PoliteClient` + MockTransport 404
- [x] **TSK-003**: API 404 — Requirement: FR-2; Deliverables: `src/krisha/api/app.py`
  - Факт: ветка до `RuntimeError`; AC-2.2 — 404, detail, повтор из негативного кэша (1 вызов на 2 запроса)
- [x] **TSK-004**: Бот: снятое объявление, `/track`, ответ на любую ошибку — Requirement: FR-4; Deliverables: `src/krisha/bot.py`
  - Факт: ответы на снятое и на непредвиденную ошибку; `/track` — `ListingNotFound` на 404; 2 теста в `test_bot.py`
- [x] **TSK-005**: Фронт показывает detail на 404 — Requirement: FR-3; Deliverables: `static/index.html`
  - Факт: ветка 404 показывает detail; живьём: локальный сервер + настоящий снятый лот 1014009074 — krisha 468 → смена сессии → 404 → на странице «Объявление не найдено — возможно, его уже сняли с продажи»
- [x] **TSK-006**: Смоук берёт другой демо-лот на 404 — Requirement: FR-5; Deliverables: `scripts/smoke_prod.py`
  - Факт: `_ListingGone` + `DEMO_REPICKS = 2`; AC-5.1 — 2 теста в `test_smoke_prod.py`
- [x] **TSK-007**: Тесты AC-1.1…AC-5.1 — Deliverables: `tests/test_predict_edge_listings.py`, `tests/test_bot.py`, `tests/test_smoke_prod.py`, `tests/e2e/test_home_e2e.py`
  - Факт: +4 юнит, +2 бота, +2 смоука, +1 e2e (e2e — в CI: Playwright только там)
- [x] **TSK-008**: Регрессии §5 — полный `pytest`, чистая копия без базы, CI
  - Факт: чистая копия без `data/krisha.db` (как CI): 678 passed, 1 failed — известное красное среды `test_static_precompress`; затронутые наборы 138/138; ruff чисто; e2e — в CI

## Progress

| Задача | Статус |
|---|---|
| TSK-001 | Complete |
| TSK-002 | Complete |
| TSK-003 | Complete |
| TSK-004 | Complete |
| TSK-005 | Complete |
| TSK-006 | Complete |
| TSK-007 | Complete |
| TSK-008 | Complete |
