---
type: file
path: tests/e2e/test_live_smoke.py
tags: [zone/tests, layer/e2e]
updated: 2026-11-03
---
# tests/e2e/test_live_smoke.py
purpose: manual live smoke (`pytest -m live`): real DB, real model, exactly one /api/predict to krisha.kz.

## Key symbols
- `test_live_health_is_fresh` — /api/health model_loaded, freshness=="ok", model_error_pct set
- `test_live_stats_and_heatmap_nonempty`
- `test_live_demo_predict_full_flow_in_browser` — "Показать на примере" demo button end-to-end

## Depends on
- [[code/tests/e2e/conftest.py]] (`live_server`), [[code/src/krisha/api/app.py]], [[concepts/scraping-krisha]]

## Data / env
- needs `data/krisha.db` (`python -m krisha.db_release`) + internet. Never in CI.

## Gotchas
- Distinct from prod smoke script [[code/scripts/smoke_prod.py]] (tested by [[code/tests/test_smoke_prod.py]], run by [[ops/workflows/smoke]]).
