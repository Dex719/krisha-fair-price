---
type: file
path: tests/test_runtime_lock.py
tags: [zone/tests, layer/ops]
updated: 2026-11-03
---
# tests/test_runtime_lock.py
purpose: issue #119 guard — prod runtime lock must not pull plotting/training deps (matplotlib, plotly, graphviz, pillow...).

## Key symbols
- imports `scripts.gen_runtime_lock` (`PLOTTING_TAIL`, `filter_runtime...`) — reason for `pythonpath=["."]` in pyproject.

## Depends on
- [[code/scripts/gen_runtime_lock.py]]

## Gotchas
- CI installs train lock, prod uses runtime lock — versions can differ ([[concepts/ci-pipeline]]).
