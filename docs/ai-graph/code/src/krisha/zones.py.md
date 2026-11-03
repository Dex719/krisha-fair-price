---
type: file
path: src/krisha/zones.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/zones.py
purpose: Fix district/microdistrict from coordinates using OSM polygons; detect shared ЖК pins.
concepts: [[concepts/location-features]]
## Key symbols
- `ZoneIndex` — index over models/osm_zones.json: district (point-in-polygon, admin_level=6), microdistrict (polygons or nearest centroid ≤ 1.2 km), shared_pin_count `src/krisha/zones.py:60`
- `load_zone_index` — missing snapshot → None, everything unchanged `src/krisha/zones.py:123`
- `resolve_zones` — repairs district/microdistrict + flag district_mismatch `src/krisha/zones.py:131`
- `approximate_pin_note` — «координаты примерные» badge when point is a ЖК pin `src/krisha/zones.py:179`
## Depends on
- [[code/src/krisha/config.py]]
## Used by
- [[code/src/krisha/features.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/train.py]]
- [[code/scripts/compare_models.py]]
- [[code/scripts/dedup_stats.py]]
- [[code/scripts/fetch_osm_zones.py]]
- tests: [[code/tests/_dir|tests/test_zones.py]]
## Data / env
- models/osm_zones.json built by [[code/scripts/fetch_osm_zones.py]].
## Gotchas
- krisha microdistrict empty in ~57% of listings; ~15% sit on one shared ЖК point.
