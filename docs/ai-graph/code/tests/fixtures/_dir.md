---
type: dir
path: tests/fixtures/
tags: [zone/tests]
updated: 2026-11-03
---
# tests/fixtures/
purpose: static input samples for parser tests (no test files here).

## Files
| File | Used by | What |
|---|---|---|
| detail_sample.html | [[code/tests/_dir]] (test_detail_parser.py, test_alerts_batch.py, test_rent_serving.py etc. via parse_detail) | saved krisha.kz listing detail page for [[code/src/krisha/scraping/detail_parser.py]] |

## Gotchas
- E2E JSON fixtures live separately in `tests/e2e/fixtures/` → [[code/tests/e2e/_dir]].
