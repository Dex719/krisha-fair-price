# Micro Spec: приложение и тесты не запускаются на Windows

**Type:** Bug Fix (локальная разработка)
**Effort:** 1 час
**Date:** 2026-09-23

## What
`src/krisha/api/app.py` импортирует `fcntl` безусловно — модуля нет на Windows, и
`import krisha.api.app` падает с `ModuleNotFoundError`. Не стартует uvicorn
(конфиг `api` в `.claude/launch.json` — Windows-venv), не собираются тесты,
импортирующие приложение (первый же — `test_about_page.py`).

## Why
README обещает локальную разработку на Windows (`Windows: .venv\Scripts\activate`,
«локальная разработка на Windows/macOS — `pip install -e ".[dev]"`»), а владелец
работает на Windows: сейчас проверить что-либо локально можно только через WSL.
`fcntl` нужен ровно в одном месте — стартовой блокировке между воркерами uvicorn
(`_startup_lock`), а на Windows приложение запускают одним процессом.

## How
- `app.py`: `fcntl` импортируется в `try/except ImportError`; без него
  `_startup_lock` — no-op (делить подготовку не с кем). Linux/прод не меняется.
- `tests/test_multiworker_safety.py`: тест, который держит `flock` в дочернем
  процессе через `fork`, на Windows пропускается (`skipif`) — это POSIX-механика.
- Прогнать весь набор нативно на Windows; что упадёт по другим причинам — чинить,
  если это баг кода, или явно описать здесь. Нашлось и починено:
  - `scripts/publish_snapshot.py::_edit_notes` писал заметки релиза во временный
    файл без `encoding` — на Windows (cp1252) кириллица падала `UnicodeEncodeError`;
    теперь `encoding="utf-8"` (gh и так читает файл как UTF-8);
  - `test_static_precompress` на Windows падал из-за `webp`/`woff2` без MIME-типа —
    это прод-баг, починен отдельно (`static-binary-headers`).

## Acceptance
`python -m uvicorn krisha.api.app:app` стартует на Windows; `pytest --ignore=tests/e2e`
на Windows собирается и проходит (кроме явно описанных исключений); на Linux
(WSL/CI) — без изменений.

## Files
- `src/krisha/api/app.py`, `tests/test_multiworker_safety.py`, `scripts/publish_snapshot.py`
