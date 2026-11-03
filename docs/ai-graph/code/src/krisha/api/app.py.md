---
type: file
path: src/krisha/api/app.py
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# src/krisha/api/app.py
purpose: FastAPI app: /api/predict, /api/health, stats/heatmap/forecast/demo/metrics, Telegram webhook, in-memory static site with live numbers, SEO endpoints, security headers, rate limiting, startup data/model download and cache warmup.
concepts: [[concepts/web-api]], [[concepts/prod-runtime]], [[concepts/frontend]]
## Key symbols
- `_lifespan` — startup hook → _startup `src/krisha/api/app.py:75`
- `CSP` — Content-Security-Policy (extended by site_analytics when GA4 set) `src/krisha/api/app.py:95`
- `MAX_BODY_BYTES` — 64 KiB request body limit (_ChunkedBodyLimitMiddleware, raw ASGI) `src/krisha/api/app.py:137`
- `DATA_STALE_AFTER_HOURS` — 30 h freshness threshold `src/krisha/api/app.py:138`
- `health` — GET /api/health: model metrics, data freshness, tg webhook status, revision `src/krisha/api/app.py:393`
- `_client_ip` — rate-limit IP: X-Bagam-Client-IP if PROXY_KEY matches, else hops-th XFF from the right (TRUSTED_PROXY_HOPS=1) `src/krisha/api/app.py:659`
- `RATE_LIMIT_TOTAL` — env RATE_LIMIT_PER_WINDOW=15 per RATE_LIMIT_WINDOW_S=60, split per worker `src/krisha/api/app.py:598`
- `_rate_subject` — IPv4 address or IPv6 /64 `src/krisha/api/app.py:705`
- `_PREDICT_LIMITER` — anyio CapacityLimiter(10) for heavy path `src/krisha/api/app.py:700`
- `predict` — POST /api/predict: cache peek BEFORE rate limit → gate in thread with PREDICT_WAIT_S=20 → error mapping 422/404/502/503 `src/krisha/api/app.py:777`
- `demo` — GET /api/demo?deal=prodazha|arenda: random URL from cached pool of 100 fresh active lots (own rate bucket) `src/krisha/api/app.py:925`
- `stats` — GET /api/stats (SWR TTLCache 600 s) `src/krisha/api/app.py:971`
- `rent_stats` — GET /api/stats/rent `src/krisha/api/app.py:987`
- `heatmap` — GET /api/heatmap `src/krisha/api/app.py:1001`
- `forecast` — GET /api/forecast (404 unless FEATURE_FORECAST) `src/krisha/api/app.py:1015`
- `api_metrics` — GET /api/metrics per-worker counters `src/krisha/api/app.py:1036`
- `telegram_webhook` — POST /tg/webhook: secret header check, update_id dedup (deque + DB), background processing (BOT_UPDATE_CONCURRENCY=4) `src/krisha/api/app.py:1092`
- `_startup_lock` — fcntl flock so one worker prepares data (no-op on Windows) `src/krisha/api/app.py:1129`
- `_prepare_data` — pull_state → ensure_db/models (+rent) → init_db `src/krisha/api/app.py:1247`
- `_warmup_runtime_caches` — load models, spatial refs, POI index, stats/heatmap/demo caches `src/krisha/api/app.py:1277`
- `_start_session_warmup` — warm krisha sticky session in background (KRISHA_SESSION_WARMUP=0 disables) `src/krisha/api/app.py:1191`
- `_render_pages` — base HTML + live numbers, JSON-LD, counters → in-memory assets `src/krisha/api/app.py:1403`
- `readyz` — model + DB on disk + pages in memory `src/krisha/api/app.py:1576`
- `sitemap_xml` — /sitemap.xml `src/krisha/api/app.py:1665`
- `_CachedStatic` — /static mount from precompressed memory `src/krisha/api/app.py:1787`
## Depends on
- [[code/src/krisha/_dir]]
- [[code/src/krisha/api/cache.py]]
- [[code/src/krisha/api/live_pages.py]]
- [[code/src/krisha/api/metrics.py]]
- [[code/src/krisha/api/schemas.py]]
- [[code/src/krisha/api/site_analytics.py]]
- [[code/src/krisha/api/static_cache.py]]
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/db_release.py]]
- [[code/src/krisha/forecast.py]]
- [[code/src/krisha/geo.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/predict_gate.py]]
- [[code/src/krisha/scraping/client.py]]
- [[code/src/krisha/spatial.py]]
- [[code/src/krisha/stats.py]]
- [[code/src/krisha/subscriptions.py]]
- [[code/src/krisha/usage.py]]
- [[concepts/frontend]] (static/ pages)
## Used by
- [[code/scripts/restore_drill.py]]
- tests: [[code/tests/conftest.py]], [[code/tests/_dir|tests/test_about_page.py]], [[code/tests/_dir|tests/test_almaty_only.py]], [[code/tests/test_api_audit_2026_10_24.py]], [[code/tests/_dir|tests/test_api_load_hardening.py]], [[code/tests/_dir|tests/test_api_rent_stats.py]], [[code/tests/_dir|tests/test_api_security.py]], [[code/tests/_dir|tests/test_feature_flags.py]], [[code/tests/_dir|tests/test_health_freshness.py]], [[code/tests/_dir|tests/test_home_redesign.py]], [[code/tests/_dir|tests/test_market_redesign.py]], [[code/tests/test_multiworker_safety.py]], [[code/tests/_dir|tests/test_page_bot.py]], [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_page_rent.py]], [[code/tests/_dir|tests/test_page_terms.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_predict_edge_listings.py]], [[code/tests/_dir|tests/test_predict_gate.py]], [[code/tests/_dir|tests/test_predict_price_history_api.py]], [[code/tests/_dir|tests/test_probes_and_seo.py]], [[code/tests/_dir|tests/test_runtime_optimizations.py]], [[code/tests/_dir|tests/test_security_txt.py]], [[code/tests/_dir|tests/test_seo_live_pages.py]], [[code/tests/_dir|tests/test_session_warmup.py]], [[code/tests/_dir|tests/test_static_precompress.py]]
## Data / env
- Env: PROXY_KEY, TRUSTED_PROXY_HOPS, RATE_LIMIT_PER_WINDOW, RATE_LIMIT_WINDOW_S, DEMO_RATE_LIMIT_PER_WINDOW, WEB_CONCURRENCY, PREDICT_WAIT_S, HEALTH_CACHE_TTL_S, DEMO_POOL_TTL_S, BOT_UPDATE_CONCURRENCY, KRISHA_STATE_PULL, KRISHA_SESSION_WARMUP, KRISHA_LIVE_PAGES (=0 → no live numbers in HTML, tests/e2e), BUILD_REVISION, PUBLIC_BASE_URL, OMP_NUM_THREADS (+ site_analytics envs).
- Files: static/ (served from memory), data/*.db, models/*.
- Run: `uvicorn krisha.api.app:app` (Dockerfile, Makefile).
## Gotchas
- Exception order matters: ChallengeBlocked/ListingNotFound/SourceUnavailable are RuntimeError subclasses and ListingOutsideAlmaty a ValueError subclass — specific branches must stay above generic ones.
- Any other ValueError (e.g. JSONDecodeError on broken meta) → 502, not 422 with raw text.
- Leftmost XFF is client-controlled — never use it (prod bypass found on HF).
- Cache hits are served before rate limit on purpose (CGNAT crowds from one channel post).
- HF Space restarts after each rescrape/retrain, so live numbers in HTML are ≤1 day old; per-process metrics/caches with WEB_CONCURRENCY>1.
- Webhook setup runs in a thread: Telegram unreachability used to block port open ~100 s.
