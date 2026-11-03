---
type: concept
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# Location features (OSM zones, POIs, H3 spatial prices)
purpose: everything derived from listing coordinates — for the model and for the «Локация» card block.

## Components
- Zones ([[code/src/krisha/zones.py]], snapshot models/osm_zones.json from [[code/scripts/fetch_osm_zones.py]]): district by point-in-polygon (OSM admin_level=6), microdistrict by polygons or nearest centroid ≤1.2 km (n≥3), shared pin count → «координаты примерные» badge. krisha microdistrict missing in ~57%, ~15% of listings share one ЖК pin.
- POIs ([[code/src/krisha/geo.py]], models/osm_pois.json from [[code/scripts/fetch_osm_pois.py]], Overpass): dist_metro/school/kindergarten/park/supermarket/bus_stop/... km, walk_score 0–100; card warnings near big roads (0.15 km) / industrial (0.4 km).
- Spatial prices ([[code/src/krisha/spatial.py]], models/spatial_ref.json, rent: models/rent/spatial_ref.json): median ₸/m² per H3 res 7/8 hex (min 8 points), fallback → district_ppsm; KNN (15 within 1 km) computed but not used by the model.
- Center distance: `dist_center_km` to `config.ALMATY_CENTER` (43.2399, 76.9570) or model meta `city_center` ([[code/src/krisha/features.py]] `model_center`).
- Coordinate quality: `db._coords_approx` marks rows on shared pins (≥ `SHARED_PIN_MIN`=5); coords outside `ALMATY_BBOX` are nulled before features and filtered from training.

## Order in pipeline
zones → (sanitize coords) → geo POIs → derived center distance → spatial hex features. See [[concepts/feature-engineering]].

## Gotchas
- All snapshots are optional (None → NaN); they ship inside model-latest / repo models/ ([[concepts/state-storage]]).
- hex7/hex8 ₸/m² already carry local price; rectangular «golden square» and KNN lag failed ablations (comments in features.py).
