---
type: file
path: src/krisha/validity.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/validity.py
purpose: Honest evaluation helpers (issue #158): test representativeness (TVD), cluster-bootstrap MAPE CI by building, time-confounding check.
concepts: [[concepts/model-evaluation]]
## Key symbols
- `BOOTSTRAP_N` — 2000 resamples `src/krisha/validity.py:38`
- `MIN_CLUSTERS_FOR_CI` — 30 `src/krisha/validity.py:42`
- `MAX_TEST_TVD` — 0.20 — max TVD for representative test / non-confounded day mix; reused by rescrape batch control `src/krisha/validity.py:47`
- `total_variation_distance` — TVD between category distributions `src/krisha/validity.py:69`
- `representativeness` — per-column TVD of test vs full sample + worst `src/krisha/validity.py:80`
- `mape` — MAPE with maximum(y,1) guard `src/krisha/validity.py:103`
- `cluster_bootstrap_ci` — percentile CI of MAPE resampling buildings `src/krisha/validity.py:111`
- `time_confounding` — weighted per-day TVD of district mix (days with ≥ MIN_ROWS_PER_DAY=30) > MAX_TEST_TVD → confounded `src/krisha/validity.py:188`
## Depends on
- [[code/src/krisha/config.py]]
## Used by
- [[code/src/krisha/scraping/rescrape.py]]
- [[code/src/krisha/train.py]]
- [[code/scripts/backtest.py]]
- tests: [[code/tests/test_shard_plan.py]], [[code/tests/_dir|tests/test_validity.py]]
## Gotchas
- Context: early collection was capped 1000/day and went alphabetically by district → test was 78.7% Алмалинский (TVD 0.46); metric not readable as city-wide error while confounded.
