# Micro Spec: pip-audit красный — anyio и soupsieve с новыми CVE

**Type:** Bug Fix (зависимости, безопасность)
**Effort:** 30 минут
**Date:** 2026-09-23

## What
Шаг `Audit dependencies (pip-audit)` в CI красный на `requirements.lock`:
`anyio 4.14.1` — CVE-2026-63374 (critical: TLS с IDNA-именами), CVE-2026-64847,
CVE-2026-63349, исправлено в 4.14.2; `soupsieve 2.8.4` — CVE-2026-85999,
CVE-2026-86000 (ReDoS в селекторах), исправлено с 2.9.

## Why
Аудит в CI блокирующий (issue #190 §2.5), а `deploy-hf.yml` не деплоит коммит,
пока CI по нему не зелёный. CVE опубликованы 17–18.09, после последнего
прогона CI на main (17.09): красным стал бы любой следующий пуш, в том числе
PR #194 с починкой прод-смоука.

## How
- `requirements.lock`, `requirements-train.lock`: `anyio==4.14.2`,
  `soupsieve==2.9.2` (у релиза 2.9.0 на PyPI нет файлов — ставить нечего;
  2.9.2 — последний, Python ≥3.10, без зависимостей). Точечно, без
  пересборки всего резолва: остальные пины не трогаем.
- `requirements-runtime.lock` — штатным `scripts/gen_runtime_lock.py`
  (`test_runtime_lock` сверяет его с выводом генератора).
- Исключения через `--ignore-vuln` не нужны: фикс-версии есть.

## Acceptance
`pip-audit -r requirements.lock --no-deps` — без находок; `pytest` зелёный,
включая `test_runtime_lock.py`.

## Files
- `requirements.lock`, `requirements-train.lock`, `requirements-runtime.lock`
