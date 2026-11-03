---
type: file
path: src/krisha/targets.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/targets.py
purpose: Experimental target modes for the walk-forward bench (issue #131): price, ppsm, index_residual; used only by scripts/backtest.py.
concepts: [[concepts/model-evaluation]]
## Key symbols
- `TARGET_MODES` — ('price', 'ppsm', 'index_residual') `src/krisha/targets.py:43`
- `build_city_index` — weekly median ₸/m² over fixed hex7 basket from train `src/krisha/targets.py:67`
- `add_target_column` — adds target col for mode `src/krisha/targets.py:136`
- `predict_price` — inverse transform to price `src/krisha/targets.py:164`
- `freshness_weight` — 0.5 ** (age_days / half_life) `src/krisha/targets.py:190`
## Used by
- [[code/scripts/backtest.py]]
- tests: [[code/tests/_dir|tests/test_targets.py]]
## Gotchas
- Production target is still log1p(price) (`features.TARGET`); this module is bench-only.
