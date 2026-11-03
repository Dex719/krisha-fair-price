---
type: file
path: src/krisha/api/cache.py
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# src/krisha/api/cache.py
purpose: Thread-safe TTL cache primitive with per-key single-flight, stale-on-error and stale-while-revalidate.
concepts: [[concepts/web-api]]
## Key symbols
- `TTLCache` — ttl, maxsize, stale_ttl, serve_stale_while_refreshing `src/krisha/api/cache.py:48`
- `TTLCache.get_or_call` — single producer per key; others wait; optional recheck under key lock `src/krisha/api/cache.py:143`
- `TTLCache.peek` — (age, value) without producing `src/krisha/api/cache.py:120`
## Used by
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/predict_gate.py]]
- [[code/src/krisha/text_parse.py]]
- tests: [[code/tests/test_api_audit_2026_10_24.py]], [[code/tests/_dir|tests/test_api_load_hardening.py]]
## Gotchas
- Per-process: with several workers each has its own cache.
