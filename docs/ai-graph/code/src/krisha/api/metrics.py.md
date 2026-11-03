---
type: file
path: src/krisha/api/metrics.py
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# src/krisha/api/metrics.py
purpose: In-process hot-path counters and latency ring buffers (512 per route) for /api/metrics.
concepts: [[concepts/web-api]]
## Key symbols
- `bump` — named counter `src/krisha/api/metrics.py:34`
- `observe` — route latency+status `src/krisha/api/metrics.py:40`
- `snapshot` — p50/p95/p99 + counters `src/krisha/api/metrics.py:55`
## Used by
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/predict_gate.py]]
- tests: [[code/tests/conftest.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_predict_gate.py]], [[code/tests/_dir|tests/test_session_warmup.py]]
## Gotchas
- Per-process: /api/metrics shows only the worker that served the request; HF bans IP for load tests so this is the only peak visibility.
