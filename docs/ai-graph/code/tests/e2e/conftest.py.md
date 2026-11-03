---
type: file
path: tests/e2e/conftest.py
tags: [zone/tests, layer/e2e]
updated: 2026-11-03
---
# tests/e2e/conftest.py
purpose: pytest-playwright fixtures — spawn uvicorn, mock all /api/* and external hosts from JSON fixtures.

## Key symbols
- data fixtures (session): `stats_data`, `heatmap_data`, `health_data`, `predict_fair`, `predict_rent`, `predict_overpriced`, `predict_good_deal`, `predict_no_price` — `:43-139` (variants derived from predict_fair.json by deepcopy)
- `_start_server(hermetic)` — free port, uvicorn subprocess, `_wait_health` up to 90s — `:150-200`
- `hermetic_server` (session, no DB) / `live_server` (session, real data/krisha.db) — `:202`, `:214`
- `hermetic_page` — routes external resources (Telegram SDK, Leaflet CDN, tiles, krisha photos) to stubs — `:242`
- `mock_api` → `_install(predict=...)` installs routes for stats/heatmap/health/forecast/predict — `:271`

## Depends on
- [[code/src/krisha/api/app.py]] (served app), fixtures in `tests/e2e/fixtures/`

## Used by
- all files in [[code/tests/e2e/_dir]]

## Gotchas
- Two modes split by markers `e2e` (hermetic, CI) and `live` (manual). Both excluded from default run by pyproject addopts.
- health must be mocked (CI has no DB).
