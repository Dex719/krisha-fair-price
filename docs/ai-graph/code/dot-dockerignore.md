---
type: file
path: .dockerignore
tags: [zone/root, layer/build]
updated: 2026-11-03
---
# .dockerignore
purpose: trim Docker build context to what [[code/Dockerfile]] copies.
Excludes: `.git .github __pycache__ *.py[cod] *.egg-info .venv venv build dist .pytest_cache .ruff_cache catboost_info docs assets infra notebooks reports tests scripts Makefile CHANGELOG.md Dockerfile data/*.db data/*.csv .env .DS_Store .idea .vscode`.
## Gotchas
- `data/*.db` excluded: DB is downloaded at boot (`db_release.ensure_db`). Local `docker build` therefore produces an image without data — needs `KRISHA_DB_TOKEN` or `KRISHA_DB_URL` at run.
- Is itself part of the HF snapshot list in [[ops/workflows/deploy-hf]].
