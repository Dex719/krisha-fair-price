---
type: file
path: src/krisha/scraping/listing_parser.py
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# src/krisha/scraping/listing_parser.py
purpose: Parse krisha search-result pages: listing ids, card prices, and next-page detection.
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `parse_listing_ids` — unique ids from `href="/a/show/<id>"` in order of appearance `src/krisha/scraping/listing_parser.py:10`
- `parse_listing_prices` — id → card price (₸) via structural DOM matching; None if not found `src/krisha/scraping/listing_parser.py:22`
- `has_next_page` — structural paginator check, not substring match (issue #99) `src/krisha/scraping/listing_parser.py:54`
## Used by
- [[code/src/krisha/scraping/crawler.py]]
- [[code/src/krisha/scraping/rescrape.py]]
- tests: [[code/tests/_dir|tests/test_detail_parser.py]], [[code/tests/_dir|tests/test_market.py]]
## Gotchas
- Price↔id matching is structural (DOM) after a bug where prices shifted between cards; sweep relies on these card prices for price_history without opening detail pages.
