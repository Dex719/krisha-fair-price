---
type: file
path: pyproject.toml
tags: [zone/root, layer/build]
updated: 2026-11-03
---
# pyproject.toml
purpose: package metadata (`krisha-fair-price` 1.0.0, Python >=3.11, setuptools, packages in `src/`), floating deps, ruff and pytest config.

## Contents
- deps: httpx, beautifulsoup4, lxml, pandas, numpy, catboost, h3, scipy, fastapi, uvicorn[standard], pydantic, cryptography (state encryption).
- extras: `train` = shap, matplotlib, scikit-learn; `dev` = train + pytest, ruff, pytest-playwright, pyyaml (workflow YAML tests).
- ruff: line-length 100, py311, select E,F,I,W,B; ignore E501, B905.
- pytest: `testpaths=["tests"]`, `pythonpath=["."]` (tests import `scripts/`), `addopts = "-m 'not e2e and not live'"`; markers `e2e`, `live` (manual, real DB + krisha.kz), `needs_model` (real weights; skipped without KRISHA_DB_TOKEN).

## Used by
`make lock` → locks ([[code/requirements.lock]]); Dockerfile `pip install -e . --no-deps`; Dependabot pip ecosystem. [[ops/build]], [[concepts/test-suite]].

## Gotchas
- Versions here float; real installs always come from locks. Changing a dep here requires `make lock`.
