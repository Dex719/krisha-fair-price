---
type: file
path: tests/conftest.py
tags: [zone/tests]
updated: 2026-11-03
---
# tests/conftest.py
purpose: global test env (no downloads/network) + autouse isolation of module-level state and caches.

## Key symbols
- module-level `os.environ.setdefault`: `KRISHA_DB_AUTO=0`, `KRISHA_MODEL_AUTO=0`, `KRISHA_STATE_PULL=0`, `USAGE_FLUSH_SYNC=1`, `KRISHA_SESSION_WARMUP=0`, `KRISHA_LIVE_PAGES=0` — `tests/conftest.py:9-22`
- `pytest_runtest_setup(item)` — skips `needs_model` tests if `config.MODEL_PATH` missing (weights not in git, issue #74) — `:25`
- `_isolate_repo_state` (autouse) — `usage.USAGE_PATH`→tmp_path, resets `usage._state/_last_flush`, stubs `subscriptions._push_to_github` — `:41`
- `_clear_api_caches` (autouse) — clears app caches (`_freshness_cache`, `_model_meta_cache`, `_stats_cache`, `_rent_stats_cache`, `_heatmap_cache`, `_forecast_cache`, `_demo_pool_cache`), `predict_gate.clear()`, `metrics.reset()`, `app._rate.clear()`, `bot.reset_rate_limits()`, `text_parse._parse_cache.clear()` before+after — `:58`

## Depends on
- [[code/src/krisha/api/app.py]], [[code/src/krisha/api/metrics.py]], [[code/src/krisha/predict_gate.py]], [[code/src/krisha/bot.py]], [[code/src/krisha/text_parse.py]], [[code/src/krisha/usage.py]], [[code/src/krisha/subscriptions.py]], [[code/src/krisha/config.py]]

## Gotchas
- Adding a new module-level cache/limiter in src → add its reset here, else order-dependent failures (TestClient shares one IP → 15th request got 429).
- Before isolation, a TestClient visit flushed into real `data/usage_stats.json` (spec `.kiro/specs/test-state-isolation`).
- Tests that need real push must restore `_push_to_github` explicitly.
- e2e uvicorn subprocess inherits this env.
