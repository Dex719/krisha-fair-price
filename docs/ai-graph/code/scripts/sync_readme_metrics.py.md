---
type: file
path: scripts/sync_readme_metrics.py
tags: [zone/scripts, layer/docs]
updated: 2026-11-03
---
# scripts/sync_readme_metrics.py
purpose: rewrites the block between `<!-- METRICS:BEGIN -->` / `<!-- METRICS:END -->` in `README.md` from `models/model_meta.json` (current model MAPE, CI, trained_at, n_train/n_test…).
## Key symbols
- `render(meta)` :35, `apply(text, block)` :87, `main(argv)` :95
## CLI
- no args → rewrite; `--check` → exit 1 if README block is stale
## Depends on
- stdlib only; reads `models/model_meta.json` (see [[code/models/_dir]])
## Used by
- [[ops/workflows/retrain]] — `python scripts/sync_readme_metrics.py` after model publish (issue #190 §2.2: README regenerated in same commit as meta)
- tests: `tests/test_readme.py`
## Gotchas
- only the METRICS block is touched; the README "how the model changed" table is a frozen history on fixed tests, intentionally not synced (README previously showed 7.6% vs meta 7.29%)
