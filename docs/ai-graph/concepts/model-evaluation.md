---
type: concept
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# Model evaluation and validity
purpose: how model quality is measured honestly and why metric numbers carry validity flags (issues #104, #106, #153, #158).

## Steps (in [[code/src/krisha/train.py]])
1. Dedup relistings (same apartment, different ids) — `dedup_relistings`, stats in meta `dedup`.
2. Time-based holdout: test = newest `first_seen` window (14 d target, 10–20% rows, ≥500, lookback ≤45 d; bulk days → train) — `time_based_split`; actual window in meta `test_window`.
3. Purge train rows leaking via fingerprint or building group — `purge_leaked_train_rows`.
4. Early stopping on a time-based val split from train (audit 24.10).
5. Metrics: MAE/MAPE/MdAPE/R² vs baseline; `model_mape_ci` cluster bootstrap by building ([[code/src/krisha/validity.py]]); `test_representativeness` (TVD per column, threshold 0.20); `time_confounding` (30-day window) and `time_confounding_history`; `temporal_validity` = representative AND not confounded; `basis: time_holdout`.
6. Gate: old model evaluated on the same test, only rows unseen by it (≥200) → `model_gate_samples.json` → [[code/scripts/model_gate.py]] (cluster gate); any failure → fail-closed.
7. Walk-forward bench: [[code/scripts/backtest.py]] uses `fit_point_model` and [[code/src/krisha/targets.py]] experiments.

## Gotchas
- Collection history (1000/day, alphabetical districts in July 2026) made tests mostly one district (TVD 0.46) — such numbers must not be read as city-wide error.
- Reported metrics describe the model trained without the test window; production model is refit on all data.
- `metrics_history.jsonl` mixes bases over time; compare only same `basis`.
