---
type: file
path: src/krisha/factor_hints.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/factor_hints.py
purpose: Human hints for each SHAP factor with real market stats from the DB (median ₸/m² slices), cached per process.
concepts: [[concepts/sale-price-model]]
## Key symbols
- `MARKET_STATS_TTL_S` — 6 h cache `src/krisha/factor_hints.py:42`
- `market_stats` — median ₸/m² by slices of active listings; {} if no DB `src/krisha/factor_hints.py:45`
- `build_factor_hints` — adds `hint` to each factor `src/krisha/factor_hints.py:454`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/features.py]]
- [[code/src/krisha/stats.py]]
## Used by
- [[code/src/krisha/predict.py]]
- tests: [[code/tests/_dir|tests/test_alerts_batch.py]], [[code/tests/_dir|tests/test_factor_hints.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_predict_edge_listings.py]]
## Gotchas
- Must not create an empty DB when it doesn't exist (fixed).
