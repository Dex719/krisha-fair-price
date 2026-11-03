---
type: file
path: src/krisha/api/static_cache.py
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# src/krisha/api/static_cache.py
purpose: Static files read and gzipped once at startup and served from memory with ETag/304 and content-hash ?v= versioning.
concepts: [[concepts/web-api]], [[concepts/frontend]]
## Key symbols
- `Asset` — raw bytes, gzip bytes, ETags `src/krisha/api/static_cache.py:73`
- `build_site` — cache + versions + rewrite /static refs with ?v=<hash> `src/krisha/api/static_cache.py:191`
- `versioned_refs` — `/static/x.css` → `?v=<10-char hash>` `src/krisha/api/static_cache.py:148`
- `replace_origins` — hardcoded site origin in pages → target origin `src/krisha/api/static_cache.py:162`
- `negotiate` — (status, body, headers) for request `src/krisha/api/static_cache.py:306`
## Used by
- [[code/src/krisha/api/app.py]]
- tests: [[code/tests/_dir|tests/test_static_precompress.py]]
## Gotchas
- Measured: gzip per request halved homepage throughput (361 vs 706 rps).
