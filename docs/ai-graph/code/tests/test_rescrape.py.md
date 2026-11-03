---
type: file
path: tests/test_rescrape.py
tags: [zone/tests, layer/scraping]
updated: 2026-11-03
---
# tests/test_rescrape.py
purpose: sharded nightly rescrape `sweep()` behaviour (~990 lines); fake HTML cards, tmp DB.

## Key symbols (tests)
- shards: `shard_urls` districts × rooms, arenda shards
- delist safety: skip delist if shard failed / all shards empty; mass delist blocked; ban aborts early and skips delist
- anti-bot: stop on antibot signature; recaptcha policy footer is NOT antibot
- parse-rate drop → run marked suspicious (history or active-DB baseline)
- detail queue: drains backlog, skips delisted, refreshes stale actives, `max_refresh=0` disables; uses shard attribution
- quarantine out-of-range card price, keep old (#103)
- time budget soft stop → shards uncovered
- recovery pass: gap detection, user visit cannot mask gap, backfill cohort marking (#156), not recorded twice

## Depends on
- [[code/src/krisha/scraping/rescrape.py]], [[code/src/krisha/db.py]], [[code/src/krisha/scraping/client.py]], [[code/src/krisha/config.py]]

## Data / env
- autouse fixture moves `rescrape.DATA_DIR` to tmp_path (writes `rescrape_history_<deal>.json`, issue #97).

## Gotchas
- Issues referenced: #96 #97 #99 #101 #102 #103 #125 #127 #152 #156 #166. See [[concepts/scraping-krisha]].
