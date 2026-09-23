# Micro Spec: тесты пишут в боевой `data/usage_stats.json`

**Type:** Bug Fix
**Effort:** 30 минут
**Date:** 2026-09-23

## What
Тесты, которые открывают страницы через `TestClient`, записывают «визиты» в
настоящий `data/usage_stats.json` репозитория.

## Why
`conftest.py` не уводит `usage.USAGE_PATH` во временную папку: первый же
`record_event("site")` флашится (`USAGE_FLUSH_SYNC=1`) прямо в рабочую копию.
Найдено 2026-09-23: базовый прогон `pytest` изменил боевую статистику
(`site: 1 → 2`). Файл коммитится ботами прода — локальный прогон легко утащит
фальшивые цифры в main, а при заданном у разработчика `GITHUB_PAT`
`save_json_state` ещё и сам запушил бы их в GitHub.

## How
- Autouse-фикстура в `tests/conftest.py`: `usage.USAGE_PATH` → `tmp_path`,
  `usage._state` / `usage._last_flush` сброшены — состояние не течёт между тестами.
- Там же `subscriptions._push_to_github` по умолчанию заглушен: тесты никогда не
  ходят в GitHub. Тесты, проверяющие сам пуш (`test_state_encryption.py`), и так
  явно возвращают настоящую функцию.

## Acceptance
Полный прогон `pytest` не меняет `git status` рабочей копии; тесты `test_usage.py`,
`test_multiworker_safety.py`, `test_state_encryption.py` зелёные.

## Files
- `tests/conftest.py` — фикстура изоляции состояния.

## Evidence (2026-09-23)
До фикса базовый прогон `pytest` изменил `data/usage_stats.json` (`site: 1 → 2`);
после — полный прогон (669 passed) оставил `git status` без изменений в `data/`.
