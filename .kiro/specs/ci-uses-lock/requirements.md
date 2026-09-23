# Micro Spec: CI тестирует не те версии, что работают на проде

**Type:** Bug Fix (CI)
**Effort:** 1 час
**Date:** 2026-09-23

## What
`ci.yml` ставит зависимости через `pip install -e ".[dev]"` — из диапазонов
`pyproject.toml`, то есть свежие версии на день прогона. Прод (Dockerfile) ставит
`requirements-runtime.lock`. На 2026-09-23 это разные библиотеки:

| Пакет | CI | Прод (lock) |
|---|---|---|
| starlette | 1.7.0 | 1.3.1 |
| fastapi | 0.141.1 | 0.139.0 |
| pandas | 3.0.6 | 3.0.3 |
| uvicorn | 0.53.0 | 0.50.0 |
| anyio | 4.15.1 | 4.14.2 |

## Why
Зелёный CI не доказывает, что работает прод. Живой пример — баг статики
(`static-binary-headers`): `starlette 1.3.1` жмёт webp/woff2 gzip-ом, 1.7.0 — нет;
тест, который это ловил, был зелёным в CI и красным на прод-версиях с 17.09, и
его списывали на «шум среды». Ровно так же любая разница поведения между
версиями уезжает в прод незамеченной.

## How
- Обе джобы `ci.yml` (`test`, `e2e`): `pip install -r requirements-train.lock`
  (= `requirements.lock` + зависимости обучения: `runtime ⊆ lock ⊆ train`,
  версии совпадают — проверено), затем пакет `--no-deps`, затем инструменты
  разработки (`pytest`, `ruff`, `pytest-playwright`), прижатые `-c
  requirements-train.lock`, чтобы их транзитивы не сдвинули пины.
- `pip-audit` по-прежнему аудитит `requirements.lock`.
- Обновление зависимостей — как раньше: `make lock` и оба lock в том же PR
  (README); теперь CI проверяет именно то, что попало в lock.

## Acceptance
CI зелёный на lock-версиях (после `static-binary-headers`); в логе установки
CI — `starlette==1.3.1`, `fastapi==0.139.0`, как в `requirements-runtime.lock`.

## Files
- `.github/workflows/ci.yml`
