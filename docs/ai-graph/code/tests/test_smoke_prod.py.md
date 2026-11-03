---
type: file
path: tests/test_smoke_prod.py
tags: [zone/tests, layer/ops]
updated: 2026-11-03
---
# tests/test_smoke_prod.py
purpose: unit tests of the prod smoke script with mocked HTTP (FakeResponse), no network.

## Key symbols
- module loaded via `importlib.util.spec_from_file_location("smoke_prod", scripts/smoke_prod.py)`
- tests: pages/API/demo predict pass; fails on missing page marker, empty API data, stale health; predict retries transient 503, fails after all-503, no retry on real 5xx; failure reports scrape counters from metrics; unavailable metrics don't mask original error; removed demo listing replaced (not breakage), only-removed → explained failure; Retry-After honoured and capped.

## Depends on
- [[code/scripts/smoke_prod.py]]

## Used by
- guards [[ops/workflows/smoke]]; context: smoke-red investigation fixed in PRs #194–#199
