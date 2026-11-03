---
type: file
path: scripts/smoke_prod.py
tags: [zone/scripts, layer/ops]
updated: 2026-11-03
---
# scripts/smoke_prod.py
purpose: production smoke check of the live HF Space (default `https://dex719-krisha-fair-price.hf.space`). See [[concepts/prod-runtime]], [[concepts/web-api]].
## Checks (`_run_with_client` :60)
- `GET /api/health` — must contain `model_error_pct`; `freshness == "ok"` and numeric `data_age_hours`
- `GET /api/demo` → live `krisha.kz/a/show/` URL; `POST /api/predict` with it → positive `fair_price`
- `GET /api/stats` (`total_listings` + market data), `GET /api/heatmap` (non-empty list), `GET /api/forecast` (non-empty; or skipped if disabled by feature flag)
- HTML pages via `PAGE_MARKERS` :25 — structural hooks, not styling: `/` (`class="logo"`, `baǵam`, `id="lotUrl"`, `data-check`, `/api/predict`), `/stats` (`data-meta="districts"`, `/api/stats`), `/about` (`О проекте`, `data-l="mape"`)
## Key symbols
- `SmokeError`, `_ListingGone` (predict 404 → pick another demo lot, `DEMO_REPICKS=2` :41)
- `_post_predict` :171 — retries on 503: `PREDICT_ATTEMPTS=3`, `PREDICT_RETRY_WAIT_S=5.0`, max 15 s, honours Retry-After
- `_scrape_counters_note` :201 — appends `/api/metrics` `counters` (`scrape_*`) to failure text (SafeLine diagnosis)
## CLI
- `--base-url` (env `PROD_BASE_URL`), `--timeout` (env `SMOKE_TIMEOUT_S`, default 30)
## Depends on
- httpx only (no src imports)
## Used by
- [[ops/workflows/smoke]] (cron `17 3,15 * * *`): `python scripts/smoke_prod.py`
- tests `tests/test_smoke_prod.py`
## Gotchas
- redesign #174 renamed `id="url"`→`id="lotUrl"`; smoke only noticed after prod rollout → markers on structural hooks
- red smoke can mean krisha.kz SafeLine challenge, not our outage (specs safeline-468, predict-edge-listings in [[history/kiro-specs]])
