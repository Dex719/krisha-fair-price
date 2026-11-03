---
type: file
path: scripts/fetch_osm_zones.py
tags: [zone/scripts, layer/ml, layer/geo]
updated: 2026-11-03
---
# scripts/fetch_osm_zones.py
purpose: OSM district/microdistrict polygons of Almaty (Overpass) + DB-derived centroids and shared pins → committed snapshot `models/osm_zones.json`. Fixes krisha's own zones (microdistrict missing in ~57% of listings or contradicting coords).
## Output content
- 8 city districts (admin_level=6) as polygons; microdistricts (place=suburb/neighbourhood/quarter) polygons matched to krisha labels by majority of labelled listings inside
- `micro_centroids` — fallback centroids of krisha microdistrict labels computed from the DB
- `shared_pins` — coords carrying many listings (ЖК pin, not a house) → UI badge «координаты примерные» (threshold `zones.SHARED_PIN_MIN`)
## CLI
- none
## Depends on
- [[code/src/krisha/config.py]] (`DB_PATH`, `OSM_ZONES_SNAPSHOT_PATH`), [[code/src/krisha/zones.py]] (`SHARED_PIN_MIN`, `point_in_polygon`); httpx
## Used by
- manual, no workflow; snapshot consumed by [[code/src/krisha/zones.py]] (`resolve_zones`, used in train/backtest/compare_models)
## Data / env
- reads listings from `data/krisha.db`; Overpass API (same mirrors/UA rule as [[code/scripts/fetch_osm_pois.py]])
