---
type: file
path: tests/test_multiworker_safety.py
tags: [zone/tests, layer/api]
updated: 2026-11-03
---
# tests/test_multiworker_safety.py
purpose: guards for WEB_CONCURRENCY > 1: shared usage file, Telegram update dedup, startup data prep.

## Key symbols (tests)
- usage: second worker doesn't erase first's counters; `merge_states` keeps days from both; flush non-blocking
- bot: `remember_update_id` across processes; dedup table bounded; missing DB doesn't silence bot
- startup waits for worker preparing data; Dockerfile runs several workers; rate limit split between workers but never degenerates to 1; worker count reads WEB_CONCURRENCY

## Depends on
- [[code/src/krisha/usage.py]], [[code/src/krisha/db.py]], [[code/src/krisha/api/app.py]], `Dockerfile`

## Gotchas
- Prod = HF Space free 2 vCPU → [[concepts/prod-runtime]].
