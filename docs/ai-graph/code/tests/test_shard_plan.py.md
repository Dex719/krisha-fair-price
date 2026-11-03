---
type: file
path: tests/test_shard_plan.py
tags: [zone/tests, layer/scraping]
updated: 2026-11-03
---
# tests/test_shard_plan.py
purpose: acceptance tests of issue #166 — daily backfill portion sharded by district × rooms.

## Key symbols (tests)
- quotas proportional to stock, largest-remainder deterministic, zero-stock gets zero (not redistributed); daily batch TVD ≤ 0.20 vs stock
- rotation: order shifts, offset advances, killed pass doesn't freeze rotation, bounded uncovered runs
- cursors survive restart, wrap to head, stop at first failed lot, poison lot doesn't stall forever
- failing shard keeps its quota unused in same pass; partial/banned shard keeps sightings; deeper than max_pages = not covered
- starved shard flag after streak (`STARVED_SHARD_STREAK`); plan vs fact per shard; stats keyed by run_seq not started_at
- `init_db` backfills shards from details once; fresh-first window

## Depends on
- [[code/src/krisha/scraping/shard_plan.py]], [[code/src/krisha/scraping/rescrape.py]], [[code/src/krisha/db.py]], [[code/src/krisha/validity.py]]

## Gotchas
- Also refs #152 #168 #190. Total requests per pass must not grow.
