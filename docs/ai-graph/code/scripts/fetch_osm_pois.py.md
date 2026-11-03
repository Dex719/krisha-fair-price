---
type: file
path: scripts/fetch_osm_pois.py
tags: [zone/scripts, layer/ml, layer/geo]
updated: 2026-11-03
---
# scripts/fetch_osm_pois.py
purpose: one-off OSM POI dump for Almaty via Overpass (roadmap stage 3) → committed snapshot `models/osm_pois.json` used for distance/walk-score features.
## Details
- categories: metro, school, kindergarten, park, supermarket, bus_stop, big_road (motorway/trunk/primary), industrial (landuse); polygons/lines thinned to points every ~60 m
- `BBOX = "43.10,76.70,43.45,77.15"`; Overpass mirrors: overpass-api.de, overpass.kumi.systems, overpass.private.coffee
- honest UA `krisha-fair-price/1.0 (...)` — Overpass answers 406 to browser UAs
## CLI
- none
## Depends on
- [[code/src/krisha/config.py]] (`OSM_POIS_SNAPSHOT_PATH`); httpx
## Used by
- manual, no workflow; snapshot consumed by [[code/src/krisha/features.py]] → [[concepts/sale-price-model]], [[concepts/rent-model]]
## Data / env
- output `{"fetched_at", "bbox", "pois": {category: [[lat, lon], ...]}}` (counts in [[code/models/_dir]])
