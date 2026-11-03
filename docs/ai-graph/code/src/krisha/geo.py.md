---
type: file
path: src/krisha/geo.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/geo.py
purpose: OSM POI features (roadmap stage 3): distances to metro/school/kindergarten/park/supermarket/bus stop/big road/industrial, walk_score, and the «Локация» card block.
concepts: [[concepts/location-features]]
## Key symbols
- `POI_DISTANCE_FEATURES` — category → dist_*_km feature `src/krisha/geo.py:22`
- `GEO_FEATURES` — dist_* + walk_score `src/krisha/geo.py:38`
- `PoiIndex` — KD-trees per category on local equirectangular projection `src/krisha/geo.py:52`
- `load_poi_index` — models/osm_pois.json → index; missing → None (NaN features) `src/krisha/geo.py:87`
- `walk_score` — 0–100, per-category linear decay to thresholds `src/krisha/geo.py:96`
- `add_geo_features` — adds features; no coords/snapshot → NaN `src/krisha/geo.py:116`
- `build_location_details` — card block with nearest POIs and warnings (BIG_ROAD_WARN_KM 0.15, INDUSTRIAL_WARN_KM 0.4) `src/krisha/geo.py:155`
## Depends on
- [[code/src/krisha/config.py]]
## Used by
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/features.py]]
- [[code/src/krisha/predict.py]]
- tests: [[code/tests/_dir|tests/test_runtime_optimizations.py]]
## Data / env
- models/osm_pois.json built once by [[code/scripts/fetch_osm_pois.py]] (Overpass API).
