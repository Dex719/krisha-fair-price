---
type: file
path: src/krisha/spatial.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/spatial.py
purpose: Spatial price features: median ₸/m² per H3 hex (res 7 ~5 km², res 8 ~0.7 km²) and KNN spatial lag; reference built on train and saved to spatial_ref.json.
concepts: [[concepts/location-features]], [[concepts/feature-engineering]]
## Key symbols
- `HEX_RES_COARSE` — 7; HEX_RES_FINE 8; HEX_MIN_N 8 `src/krisha/spatial.py:29`
- `KNN_K` — 15 neighbours within KNN_MAX_KM=1.0, KNN_MIN_N=3 `src/krisha/spatial.py:32`
- `SPATIAL_FEATURES` — hex7_ppsm, hex8_ppsm, knn_ppsm, knn_n `src/krisha/spatial.py:36`
- `build_spatial_ref` — train df → points + hex medians `src/krisha/spatial.py:75`
- `self_indices_for` — positions of train rows in ref (to exclude self from KNN) `src/krisha/spatial.py:64`
- `load_spatial_ref` — cached load; missing → None (features fall back) `src/krisha/spatial.py:104`
- `add_spatial_features` — fallback chain fine → coarse → district_ppsm `src/krisha/spatial.py:190`
## Depends on
- [[code/src/krisha/config.py]]
## Used by
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/features.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/rental_yield.py]]
- [[code/src/krisha/train.py]]
- [[code/scripts/compare_models.py]]
- tests: [[code/tests/_dir|tests/test_factor_hints.py]], [[code/tests/test_model_artifacts.py]], [[code/tests/_dir|tests/test_rent_serving.py]], [[code/tests/_dir|tests/test_runtime_optimizations.py]], [[code/tests/_dir|tests/test_spatial.py]]
## Data / env
- models/spatial_ref.json (sale), models/rent/spatial_ref.json (rent).
## Gotchas
- Rent must load the rent spatial_ref (bug fixed: rent used the sale ref).
