---
type: file
path: src/krisha/train.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/train.py
purpose: Training pipeline (CatBoost on log1p(price)) for sale and rent: load, clean, dedup relistings, time-based split + purge, point model with early stopping, MultiQuantile+CQR interval, validity metrics, old-model comparison for the gate, refit on all data, save artifacts.
concepts: [[concepts/sale-price-model]], [[concepts/rent-model]], [[concepts/model-evaluation]], [[concepts/price-interval-cqr]]
## Key symbols
- `TEST_WINDOW_DAYS` — 14 — test = newest first_seen window `src/krisha/train.py:47`
- `TEST_MAX_FRACTION` — 0.20 cap (issue #153); TEST_MIN_FRACTION 0.10, TEST_MIN_ROWS 500, MAX_TEST_LOOKBACK_DAYS 45 `src/krisha/train.py:62`
- `BULK_DAY_MULTIPLIER` — 3.0× median daily inflow (and ≥1500 rows) = bulk day → forced into train `src/krisha/train.py:70`
- `load_dataset` — SELECT * FROM listings; drops source='user' (issue #117) and stale/out-of-bbox rows `src/krisha/train.py:117`
- `_filter_stale_and_out_of_area` — keep active or delisted ≤ STALE_DELISTED_DAYS; drop coords outside ALMATY_BBOX `src/krisha/train.py:75`
- `dedup_relistings` — same fingerprint & price within 2% → one apartment (DEDUP_PRICE_TOLERANCE) `src/krisha/train.py:181`
- `building_groups` — building group = coords rounded ~10 m, else id `src/krisha/train.py:271`
- `time_based_split` — issue #104: test = most recent by first_seen with min/max fraction and bulk-day handling `src/krisha/train.py:282`
- `purge_leaked_train_rows` — remove train rows leaking into test via fingerprint or building group `src/krisha/train.py:410`
- `baseline_predict` — median ₸/m² by (district, rooms) × area `src/krisha/train.py:438`
- `POINT_ITERATIONS` — 4000 tree ceiling (ES picks ~3400–3999) `src/krisha/train.py:502`
- `GATE_MIN_UNSEEN_ROWS` — 200 — fewer test rows unseen by old model → gate fail-closed `src/krisha/train.py:506`
- `CQR_COVERAGE_MARGIN` — 0.01 — calibrate at 81% to hit ~80% actual coverage `src/krisha/train.py:518`
- `_cqr_scale` — conformal quantile at ⌈(n+1)·c⌉/n with n = number of buildings `src/krisha/train.py:521`
- `fit_quantile_interval` — MultiQuantile q10+q90 (issue #132) + CQR calibration `src/krisha/train.py:569`
- `fit_point_model` — probe with ES on time-based val (audit 24.10) → final model on all raw_train; shared with scripts/backtest.py `src/krisha/train.py:842`
- `train` — full pipeline → metrics dict; writes model.cbm, model_quantile.cbm, spatial_ref.json, model_meta.json, SHAP png, metrics_history, stats snapshot (sale) `src/krisha/train.py:938`
- `_unseen_by_old_model` — test rows newer than old model's max_first_seen and not relistings it saw `src/krisha/train.py:1240`
- `_typical_values` — median/mode of rent features → meta feature_defaults (used by rental_yield) `src/krisha/train.py:1322`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/features.py]]
- [[code/src/krisha/interval.py]]
- [[code/src/krisha/model_spec.py]]
- [[code/src/krisha/monitoring.py]]
- [[code/src/krisha/rent.py]]
- [[code/src/krisha/spatial.py]]
- [[code/src/krisha/stats.py]]
- [[code/src/krisha/validity.py]]
- [[code/src/krisha/zones.py]]
## Used by
- [[code/scripts/backtest.py]]
- [[code/scripts/compare_models.py]]
- [[code/scripts/dedup_stats.py]]
- [[code/scripts/train.py]]
- [[ops/workflows/retrain]]
- tests: [[code/tests/_dir|tests/test_city_center.py]], [[code/tests/_dir|tests/test_interval.py]], [[code/tests/test_model_artifacts.py]], [[code/tests/_dir|tests/test_rent_model.py]], [[code/tests/_dir|tests/test_rent_serving.py]], [[code/tests/_dir|tests/test_spatial.py]], [[code/tests/_dir|tests/test_train_audit.py]], [[code/tests/_dir|tests/test_train_refit.py]], [[code/tests/_dir|tests/test_train_smoke.py]]
## Data / env
- Reads data/krisha.db or data/krisha_rent.db (spec.db_path).
- Writes models/{model.cbm, model_quantile.cbm, model_meta.json, spatial_ref.json, model_gate_samples.json, metrics_history.jsonl, stats.json} (rent: models/rent/…), reports/shap_summary*.png.
## Gotchas
- Metrics are measured on the model WITHOUT the test window; production model is refit on ALL data (`refit_on_all`) — shipped model slightly better than reported (−0.29 pp MAPE on backtest).
- ppsm_maps/spatial_ref are built only on the fit part for ES probe and only on train for metrics (no target leakage); KNN excludes self on train.
- Old model comparison only on rows unseen by it; failure → metrics['old_model_error'] → gate blocks (issue #106).
- Quantile model is refit on all data with ES-found iterations; CQR scale carried over from calibration.
- `temporal_validity` = test representative (TVD ≤ 0.20) AND not time-confounded (30-day window) — see [[code/src/krisha/validity.py]].
- Uses `sqlite3.connect` directly (allowlisted).
