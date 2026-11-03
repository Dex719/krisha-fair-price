---
type: file
path: src/krisha/features.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/features.py
purpose: Data cleaning and feature engineering shared by training and inference (sale and rent).
concepts: [[concepts/feature-engineering]], [[concepts/sale-price-model]], [[concepts/rent-model]], [[concepts/location-features]]
## Key symbols
- `RAW_PARAM_CAT_MAP` — categorical features from raw_params (renovation, toilet, furniture, parking, balcony, …) `src/krisha/features.py:26`
- `CAT_FEATURES` — district, microdistrict, building_type, complex_name, user_type, category, raw-param cats, housing_class, developer `src/krisha/features.py:55`
- `NUM_FEATURES` — rooms, area, floors, building_age, ceiling, lat/lon, dist_center_km, photos_count, is_new_building, district/microdistrict_ppsm, security flags, complex nums, GEO_FEATURES, hex7_ppsm, hex8_ppsm `src/krisha/features.py:60`
- `EXTRA_FEATURES` — computed but NOT in model: district_mismatch, knn_ppsm, knn_n, in_golden_square, otbasy_panel_excluded `src/krisha/features.py:106`
- `RENT_NUM_EXTRA` — rent-only numeric (balcony_n, loggia_n, toilet_count, facilities/who flags…) `src/krisha/features.py:135`
- `RENT_CAT_EXTRA` — rent-only categoricals (bathroom, window_side, priv_dorm…) `src/krisha/features.py:140`
- `TARGET` — 'log_price' = log1p(price) `src/krisha/features.py:143`
- `MISSING_CAT` — 'unknown' `src/krisha/features.py:144`
- `current_year` — function, not constant (building_age) `src/krisha/features.py:156`
- `_sanitize_raw_numeric` — broken raw values → NaN before derived features (issue #108) `src/krisha/features.py:166`
- `clean` — drop rows without price/area and outside price/ppsm bounds `src/krisha/features.py:214`
- `compute_ppsm_maps` — median ₸/m² by district/microdistrict + global — train part only `src/krisha/features.py:234`
- `add_raw_param_features` — renovation/toilet/furniture/parking/balcony/security from raw_params `src/krisha/features.py:275`
- `add_complex_features` — join ЖК attrs via complexes lookup `src/krisha/features.py:380`
- `build_features` — full pipeline: resolve_zones → raw params → complex → sanitize → geo → derived → cats → ppsm maps → spatial → log_price → inf→NaN `src/krisha/features.py:394`
- `model_center` — center point stored in model meta (city_center) else LEGACY_ALMATY_CENTER `src/krisha/features.py:505`
- `listing_to_frame` — one listing dict → feature frame (predict) `src/krisha/features.py:514`
- `listings_to_frame` — batch version (alerts) `src/krisha/features.py:524`
## Depends on
- [[code/src/krisha/complexes.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/geo.py]]
- [[code/src/krisha/spatial.py]]
- [[code/src/krisha/zones.py]]
## Used by
- [[code/src/krisha/factor_hints.py]]
- [[code/src/krisha/model_spec.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/rental_yield.py]]
- [[code/src/krisha/train.py]]
- [[code/scripts/backtest.py]]
- [[code/scripts/compare_models.py]]
- [[code/scripts/dedup_stats.py]]
- tests: [[code/tests/_dir|tests/test_city_center.py]], [[code/tests/_dir|tests/test_complexes.py]], [[code/tests/_dir|tests/test_features.py]], [[code/tests/_dir|tests/test_home_redesign.py]], [[code/tests/test_model_artifacts.py]], [[code/tests/_dir|tests/test_predict_edge_listings.py]], [[code/tests/_dir|tests/test_rent_model.py]], [[code/tests/_dir|tests/test_train_audit.py]]
## Gotchas
- Coordinates sanitized BEFORE geo features; out-of-bbox coords used to yield plausible POI distances with 900 km to center.
- knn_ppsm/knn_n excluded: on train the neighbour is the same building, absent on test (leakage mismatch). LLM flag features removed in #157 (R² 0.79→0.76).
- Golden-square and Otbasy-panel features failed paired walk-forward AB (Aug 2026) — kept only as candidates/badges.
- price=None ('договорная') must not crash log1p → to_numeric(errors='coerce').
- Inference must use `model_center(meta)`, not current ALMATY_CENTER (center changed 10.2026).
