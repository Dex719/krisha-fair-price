---
type: file
path: src/krisha/scraping/crawler.py
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# src/krisha/scraping/crawler.py
purpose: Legacy resumable full crawler: general search pages → ids → detail pages → SQLite. Superseded in prod by sharded rescrape.
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `crawl` — crawl(max_pages, max_listings, skip_known) → number of new rows; stops on BanDetected `src/krisha/scraping/crawler.py:18`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/scraping/client.py]]
- [[code/src/krisha/scraping/detail_parser.py]]
- [[code/src/krisha/scraping/listing_parser.py]]
## Used by
- [[code/scripts/crawl.py]]
## Data / env
- Search URL: `config.SEARCH_URL` (https://krisha.kz/prodazha/kvartiry/almaty/). Writes `data/krisha.db` via db.upsert_listing.
## Gotchas
- Unsharded general search is truncated by krisha pagination (~7–8k popular listings of ~44k); production collection is [[code/src/krisha/scraping/rescrape.py]].
