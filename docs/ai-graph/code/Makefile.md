---
type: file
path: Makefile
tags: [zone/root, layer/build]
updated: 2026-11-03
---
# Makefile
purpose: local dev shortcuts (POSIX make; not used by CI).

## Targets
- `install` — `pip install -e ".[dev]"`.
- `lock` — `uv pip compile pyproject.toml -o requirements.lock $(LOCK_FLAGS)`; `uv pip compile pyproject.toml --extra train -c requirements.lock -o requirements-train.lock $(LOCK_FLAGS)`; `python scripts/gen_runtime_lock.py`. `LOCK_FLAGS = --python-version 3.11 --python-platform x86_64-unknown-linux-gnu $(UV_FLAGS)`. Bump one: `make lock UV_FLAGS="--upgrade-package pillow"`. [[code/scripts/gen_runtime_lock.py]]
- `crawl` (`scripts/crawl.py --pages 5 --limit 50`), `crawl-full` (`--pages 300`), `crawl-complexes` (`scripts/crawl_complexes.py --skip-known`). [[code/scripts/crawl.py]], [[code/scripts/crawl_complexes.py]]
- `rescrape` — `python scripts/rescrape.py`. [[code/scripts/rescrape.py]]
- `train` — `python scripts/train.py`. [[code/scripts/train.py]]
- `api` — `uvicorn krisha.api.app:app --reload --port 8000`.
- `test` (`pytest -q`), `test-e2e` (`pytest -m e2e -q`, needs `playwright install chromium`), `lint` (`ruff check src tests scripts`).

## Gotchas
- Train lock constrained by `-c requirements.lock` so shared packages match (they used to diverge).
- On Windows without make: run the underlying commands directly.
Related: [[ops/build]].
