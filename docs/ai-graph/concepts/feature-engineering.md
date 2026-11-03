---
type: concept
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# Feature engineering
purpose: how a raw listing row becomes model features; identical code for training and inference (sale and rent).

## Pipeline (`features.build_features`, [[code/src/krisha/features.py]])
1. `resolve_zones` — district/microdistrict repaired from OSM polygons + `district_mismatch` ([[code/src/krisha/zones.py]]).
2. `add_raw_param_features` — categorical/flag features from `raw_params` JSON (renovation, toilet, furniture, parking, balcony, security guard/intercom/video, + rent params).
3. `add_complex_features` — ЖК attrs (developer, housing_class, completion_year, apartments_count) from models/complexes.json ([[code/src/krisha/complexes.py]]).
4. Numeric coercion + `_sanitize_raw_numeric` (floors 1..100, year ≥1930, ceiling 2..5, rooms 1..10, coords in bbox) — BEFORE geo features.
5. `add_geo_features` — POI distances + walk_score ([[code/src/krisha/geo.py]]).
6. Derived: floor_ratio, first/last floor, building_age (`current_year()`), dist_center_km (center from model meta), otbasy_panel_excluded, in_golden_square.
7. Categoricals → str with 'unknown'; is_new_building.
8. district_ppsm / microdistrict_ppsm from `ppsm_maps` (built on train only, stored in meta).
9. `add_spatial_features` — hex7/hex8 ₸/m², KNN lag ([[code/src/krisha/spatial.py]]).
10. `log_price` target if price present; ±inf → NaN.

## Feature sets
- Sale: `NUM_FEATURES + CAT_FEATURES` (`ALL_FEATURES`); rent: `RENT_NUM_FEATURES + RENT_CAT_FEATURES`. The list actually used is stored in model meta `features`/`cat_features` and inference uses the meta list.
- Computed but excluded (`EXTRA_FEATURES`): district_mismatch, knn_ppsm, knn_n, in_golden_square, otbasy_panel_excluded — failed ablations/AB tests (see comments in code).

## Leakage rules
- ppsm maps, spatial ref, KNN self-exclusion and ES val maps are built on the fit part only ([[code/src/krisha/train.py]] `fit_point_model`).
- Training data cleaning: `features.clean`, `train.dedup_relistings`, purge by fingerprint/building group — see [[concepts/model-evaluation]].

## Gotchas
- Inference passes `center=model_center(meta)`; ALMATY_CENTER changed in 10.2026, old models used LEGACY center.
- Missing snapshots (OSM, complexes, spatial ref) degrade to NaN/'unknown' silently (fail-soft) — check models/ contents when features look empty.
