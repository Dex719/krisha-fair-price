---
type: concept
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# Sale price model
purpose: CatBoost model of fair asking price (₸) for Almaty apartments for sale, with a calibrated 80% interval and per-listing SHAP explanation.

## What
- Point model: `CatBoostRegressor` RMSE on target `log_price = log1p(price)`, depth 8, lr 0.05, ≤4000 trees with early stopping on a time-based val split. Spec: `model_spec.SALE` ([[code/src/krisha/model_spec.py]]).
- Interval: one `MultiQuantile:alpha=0.1,0.9` model + conformal (CQR) scale → [[concepts/price-interval-cqr]].
- Features: [[concepts/feature-engineering]] + [[concepts/location-features]].
- Verdict: GOOD_DEAL if price < interval low, OVERPRICED if > high, else FAIR; legacy ±10% if no interval model.

## Training pipeline ([[code/src/krisha/train.py]] `train()`; run by [[code/scripts/train.py]] in [[ops/workflows/retrain]], gate [[code/scripts/model_gate.py]], report [[code/scripts/notify_retrain.py]])
1. `load_dataset`: all `listings` from data/krisha.db minus `source='user'`, minus delisted > 90 days, minus coords outside Almaty bbox.
2. `prepare_frame` → `features.clean` (price 5M..1.5B, area 10..500, ₸/m² 100k..5M).
3. `dedup_relistings` (fingerprint + price within 2%).
4. `time_based_split`: test = newest first_seen (~14 d, 10–20% of rows, ≥500, bulk days forced to train) → `purge_leaked_train_rows`.
5. `fit_point_model`: ppsm maps + spatial ref from train only; ES probe on time-based val; final fit on train.
6. Metrics on test: model vs baseline (median ₸/m² by district×rooms), cluster-bootstrap MAPE CI, representativeness, time confounding, `temporal_validity` ([[code/src/krisha/validity.py]]).
7. Interval: `train_quantile_interval` (calibration on part of train) → coverage/width on test.
8. Old model on same test (rows unseen by it, ≥200) → `model_gate_samples.json` for the gate; failure → `old_model_error` → gate blocks.
9. `refit_on_all`: final point + quantile models on all data (shipped model ≠ measured model, slightly better).
10. Save: models/model.cbm, model_quantile.cbm, spatial_ref.json, model_meta.json (features, cat_features, metrics, ppsm_maps, city_center), reports/shap_summary.png, metrics_history.jsonl ([[code/src/krisha/monitoring.py]]), models/stats.json; published to release model-latest ([[concepts/state-storage]]).

## Inference ([[code/src/krisha/predict.py]])
`predict_from_url` → scrape ([[concepts/scraping-krisha]] user path) → `parse_detail` → `is_almaty` → `detect_deal` → `listing_to_frame(ppsm_maps from meta, spatial_ref, center=model_center(meta))` → `_price_pool` → SHAP `explain_price` (+ [[code/src/krisha/factor_hints.py]]) → enrichments: details, ЖК block, location ([[code/src/krisha/geo.py]], [[code/src/krisha/zones.py]]), price history/days on market/liquidity ([[code/src/krisha/market.py]]), rental yield ([[code/src/krisha/rental_yield.py]]), scam badge ([[code/src/krisha/scam.py]]), analogs ([[code/src/krisha/analogs.py]]) → `log_prediction`. Batch variant `predict_listings_batch` for alerts.

## Inputs / outputs
- In: SQLite listings; OSM snapshots models/osm_pois.json, models/osm_zones.json; models/complexes.json.
- Out: artifacts in models/ (see [[code/src/krisha/config.py]] paths); API card `PredictResponse` ([[code/src/krisha/api/schemas.py]]).

## Gotchas
- Metric numbers live in models/model_meta.json `metrics` (also exposed by /api/health); they are not comparable across different `basis`/test windows; read `temporal_validity` first.
- Meta compatibility: old metas lack `city_center` (→ LEGACY center) and `cqr_scale` (→ `cqr_offset_log`); legacy model_lo/model_hi pair still supported.
- Rejected features (kept computed only): knn_ppsm/knn_n, LLM flags (#157), golden square, Otbasy panel age.
- Production target remains log1p(price); [[code/src/krisha/targets.py]] modes are bench-only ([[code/scripts/backtest.py]]).
