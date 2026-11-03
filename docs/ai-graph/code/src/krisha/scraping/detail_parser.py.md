---
type: file
path: src/krisha/scraping/detail_parser.py
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# src/krisha/scraping/detail_parser.py
purpose: Parse a krisha listing page (/a/show/<id>) into a flat listing dict (db.LISTING_COLUMNS + extra non-DB keys).
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `parse_detail` — html → dict | None; main source is inline JSON `window.data.advert`, extra params from `[data-name]` HTML blocks `src/krisha/scraping/detail_parser.py:85`
- `extract_params` — all `data-name` → text pairs (flat.building, house.year, ceiling, flat.renovation, …) → stored as JSON in `raw_params` `src/krisha/scraping/detail_parser.py:54`
- `clean_description` — strips krisha translator tail («Перевод может быть неточным…») `src/krisha/scraping/detail_parser.py:33`
- `_parse_floor` — floor/total_floors from params («5 из 9») or title («5/9 этаж») `src/krisha/scraping/detail_parser.py:75`
## Depends on
- [[code/src/krisha/scraping/json_window.py]]
## Used by
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/scraping/crawler.py]]
- [[code/src/krisha/scraping/rescrape.py]]
- tests: [[code/tests/_dir|tests/test_alerts_batch.py]], [[code/tests/_dir|tests/test_almaty_only.py]], [[code/tests/_dir|tests/test_bot.py]], [[code/tests/_dir|tests/test_detail_parser.py]], [[code/tests/_dir|tests/test_predict_edge_listings.py]], [[code/tests/_dir|tests/test_rent_serving.py]], [[code/tests/_dir|tests/test_ux_audit_fixes.py]]
## Gotchas
- Keys `city`, `deal`, `photos` are returned but NOT persisted (not in LISTING_COLUMNS): `city` drives `predict.is_almaty`, `deal` (sectionAlias) drives `rent.detect_deal`, `photos` go to API/bot.
- Layout breakage: fix `[data-name]` selectors first; `window.data` is usually stable (verified June 2026).
- Returns None if `window.data.advert` missing/without id (crawler/sweep then count a detail failure).
