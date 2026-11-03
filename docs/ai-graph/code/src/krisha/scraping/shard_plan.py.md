---
type: file
path: src/krisha/scraping/shard_plan.py
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# src/krisha/scraping/shard_plan.py
purpose: Pure planner for per-shard detail-fetch quotas and shard rotation (issue #166/#152/#168) so each day's batch is a city mix, not a slice.
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `largest_remainder_quotas` — split daily cap across shards proportional to stock; sum exactly == cap (verified by brute force in #167 — don't change) `src/krisha/scraping/shard_plan.py:29`
- `redistribute_leftover` — 2nd planner pass: leftover quota of shallow-backlog shards → measured shards with deeper backlog; unmeasured shards get nothing `src/krisha/scraping/shard_plan.py:59`
- `rotated` — cyclic shift of shard order `src/krisha/scraping/shard_plan.py:123`
- `rotation_step` — step coprime with n_shards (step +1 caused long runs of uncovered tail shards) `src/krisha/scraping/shard_plan.py:137`
- `rotation_offset` — offset for pass_seq `src/krisha/scraping/shard_plan.py:157`
- `shard_district` — district from label «Район Nк» for batch TVD control `src/krisha/scraping/shard_plan.py:164`
## Used by
- [[code/src/krisha/scraping/rescrape.py]]
- tests: [[code/tests/_dir|tests/test_pass_plan.py]], [[code/tests/test_shard_plan.py]]
## Gotchas
- Before #166 the queue ordered by listings.district/rooms which are NULL for sighting-only rows → effectively ORDER BY id DESC; batch TVD vs base 0.32–0.35 and model_meta time_confounding stuck true.
