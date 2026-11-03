---
type: dir
path: tests/e2e/
tags: [zone/tests, layer/e2e]
updated: 2026-11-03
---
# tests/e2e/
purpose: browser (Playwright chromium) and real-HTTP tests against a spawned uvicorn; excluded from default `pytest` via addopts.

## How to run
- `pytest -m e2e -q` (CI job in [[ops/workflows/ci]], needs `pytest-playwright` + chromium). `pytest -m live` only manually.
- Fixtures in [[code/tests/e2e/conftest.py]]; JSON payloads in `tests/e2e/fixtures/` (stats.json, stats_rent.json, heatmap.json, predict_fair.json).

## Files
| Test | Covers | Checks |
|---|---|---|
| conftest.py | [[code/src/krisha/api/app.py]] | spawns uvicorn, mocks /api/* + CDNs → [[code/tests/e2e/conftest.py]] |
| test_api_over_http_e2e.py | [[code/src/krisha/api/app.py]] | API contract over real sockets, not TestClient (refs #113, #74; needs_model) |
| test_home_e2e.py | [[code/static/_dir]], [[code/src/krisha/api/app.py]] | home page JS flows → [[code/tests/e2e/test_home_e2e.py]] |
| test_home_flow_e2e.py | [[code/static/_dir]] | back/forward history, 502, silent demo retry, button race |
| test_live_smoke.py | whole stack | `live`: real DB/model, one krisha.kz request → [[code/tests/e2e/test_live_smoke.py]] |
| test_market_e2e.py | [[code/static/_dir]] | /stats page sale+rent in browser |
| test_perf_hardening_e2e.py | [[code/src/krisha/api/static_cache.py]], [[code/src/krisha/api/metrics.py]] | precompressed static, HEAD, metrics over HTTP |
| test_shell_e2e.py | [[code/static/_dir]] | site.js shell: menu, live numbers, no third-party animation libs |
| test_stats_about_e2e.py | [[code/static/_dir]] | /about, 404, page transitions |

## Gotchas
- Hermetic mode must mock /api/health too: real server answers from local DB state → green locally, red in CI without DB.
- Markup selectors are post-redesign (`form[data-check]`, `#lotUrl`, `.sheet`, `#rFair`, ...); listed in test_home_e2e.py docstring.

## Used by
- [[code/tests/_dir]], [[concepts/test-suite]], [[concepts/frontend]]
