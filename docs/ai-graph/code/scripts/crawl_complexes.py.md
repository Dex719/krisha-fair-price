---
type: file
path: scripts/crawl_complexes.py
tags: [zone/scripts, layer/scraping]
updated: 2026-11-03
---
# scripts/crawl_complexes.py
purpose: one-off scrape of the residential-complex (ЖК) catalogue (roadmap stage 2) from sitemap `https://krisha.kz/sitemap/frontend/complexes.xml`; keeps region «Алматы»; at the end refreshes snapshot `models/complexes.json` for deploy.
## CLI
- `--limit N` (0 = unlimited pages), `--all-regions` (no Almaty filter), `--skip-known` (skip already collected ЖК)
## Depends on
- [[code/src/krisha/complexes.py]] (`normalize_complex_name`, `snapshot_complexes`), [[code/src/krisha/config.py]], [[code/src/krisha/db.py]] (`init_db`, `known_complex_ids`, `upsert_complex`), [[code/src/krisha/scraping/client.py]] (`PoliteClient`, `BanDetected`), [[code/src/krisha/scraping/complex_parser.py]] (`parse_complex`)
- [[concepts/scraping-krisha]]
## Used by
- `Makefile` (`--skip-known`). No workflow.
## Data / env
- writes `complexes` table in `data/krisha.db` and `models/complexes.json` (730 ЖК; see [[code/models/_dir]])
## Gotchas
- `BanDetected` (403 series) on sitemap is caught → already collected ЖК still reach the snapshot
