---
type: file
path: src/krisha/predict_gate.py
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# src/krisha/predict_gate.py
purpose: Single gate to the heavy predict path for web and bot: per-listing TTL cache, single-flight, negative cache, short source-failure cache, process-wide slots, short user scrape timeout.
concepts: [[concepts/web-api]], [[concepts/telegram-bot]]
## Key symbols
- `PREDICT_CACHE_TTL_S` — env, default 600 s `src/krisha/predict_gate.py:63`
- `PREDICT_NEGATIVE_TTL_S` — env, default 60 s (cached errors per listing) `src/krisha/predict_gate.py:67`
- `SOURCE_FAILURE_TTL_S` — env, default 4 s (SourceUnavailable cached only seconds) `src/krisha/predict_gate.py:72`
- `PREDICT_SLOTS` — env, default 10 concurrent scrapes `src/krisha/predict_gate.py:77`
- `PREDICT_SLOT_WAIT_S` — env, default 15 s `src/krisha/predict_gate.py:80`
- `USER_SCRAPE_TIMEOUT_S` — env, default 5 s total / USER_SCRAPE_CONNECT_S 3 s `src/krisha/predict_gate.py:84`
- `PredictBusy` — slot not acquired in time → 503 `src/krisha/predict_gate.py:88`
- `cache_key` — listing id (+ vision flag) `src/krisha/predict_gate.py:110`
- `peek` — cached result without any I/O (used before rate limit) `src/krisha/predict_gate.py:143`
- `cached_predict` — validate URL → cache → failure caches → TTLCache.get_or_call(_run) `src/krisha/predict_gate.py:289`
- `warm_session` — one user-client pass to warm sticky SafeLine session at startup `src/krisha/predict_gate.py:250`
- `count_scrape_attempt` — attempt outcome → metrics counters scrape_* `src/krisha/predict_gate.py:240`
## Depends on
- [[code/src/krisha/api/cache.py]]
- [[code/src/krisha/api/metrics.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/scraping/client.py]]
## Used by
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/bot.py]]
- tests: [[code/tests/conftest.py]], [[code/tests/_dir|tests/test_almaty_only.py]], [[code/tests/test_api_audit_2026_10_24.py]], [[code/tests/_dir|tests/test_api_load_hardening.py]], [[code/tests/_dir|tests/test_api_security.py]], [[code/tests/_dir|tests/test_bot.py]], [[code/tests/_dir|tests/test_bot_audit_fixes.py]], [[code/tests/_dir|tests/test_bot_buttons.py]], [[code/tests/_dir|tests/test_bot_limits.py]], [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_predict_edge_listings.py]], [[code/tests/_dir|tests/test_predict_gate.py]], [[code/tests/_dir|tests/test_predict_price_history_api.py]], [[code/tests/_dir|tests/test_session_warmup.py]]
## Data / env
- Env: PREDICT_CACHE_TTL_S, PREDICT_NEGATIVE_TTL_S, SOURCE_FAILURE_TTL_S, PREDICT_SLOTS, PREDICT_SLOT_WAIT_S, USER_SCRAPE_TIMEOUT_S, USER_SCRAPE_CONNECT_S.
## Gotchas
- Scenario: a channel post brings 1000 users checking the same listing → one scrape for all.
- InvalidListingUrl and PredictBusy are never cached; stored exceptions are detached copies (no traceback/context).
- Bot also goes through this gate (it used to bypass it).
