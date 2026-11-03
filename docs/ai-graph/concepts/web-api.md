---
type: concept
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# Web API (FastAPI app)
purpose: single FastAPI process serving site pages, JSON API and the Telegram webhook on HF Space (domain bagam.info via Cloudflare worker) — see [[concepts/prod-runtime]].

## Endpoints ([[code/src/krisha/api/app.py]])
| route | what |
|---|---|
| POST /api/predict `{url}` | evaluate krisha listing → `PredictResponse` |
| GET /api/health | status, model metrics (MAPE, MdAPE, R², MAE, CI, temporal validity), data freshness, tg webhook status (self-heals), revision, rent model fields |
| GET /api/demo?deal= | random fresh active listing URL |
| GET /api/stats, /api/stats/rent, /api/heatmap | market stats ([[code/src/krisha/stats.py]]) via SWR TTLCache |
| GET /api/forecast | 404 unless FEATURE_FORECAST ([[code/src/krisha/forecast.py]]) |
| GET /api/metrics | per-worker counters/latencies ([[code/src/krisha/api/metrics.py]]) |
| POST /tg/webhook | Telegram updates → [[concepts/telegram-bot]] |
| /, /stats, /about, /rent (301 → /stats?mode=rent), static pages, /static/* | HTML from memory ([[code/src/krisha/api/static_cache.py]], [[code/src/krisha/api/live_pages.py]], [[concepts/frontend]]) |
| /livez, /readyz, /robots.txt, /sitemap.xml, /llms.txt, /.well-known/security.txt, /favicon.ico | probes / SEO |

## /api/predict pipeline (in order)
1. `predict_gate.peek(url)` — cache hit returned BEFORE rate limit.
2. `_check_rate_limit` — 15 req / 60 s per IP (IPv6 → /64), split across `WEB_CONCURRENCY` workers; IP from `X-Bagam-Client-IP` if `PROXY_KEY` matches, else XFF from the right (`TRUSTED_PROXY_HOPS`).
3. Thread with `_PREDICT_LIMITER` (10) and `PREDICT_WAIT_S` (20 s) → [[code/src/krisha/predict_gate.py]] `cached_predict` (TTL 600 s, single-flight, negative cache 60 s, source failures 4 s, 10 slots / 15 s wait, scrape timeout 5 s).
4. [[code/src/krisha/predict.py]] `predict_from_url` (see [[concepts/sale-price-model]], [[concepts/rent-model]]).
5. Error mapping: InvalidListingUrl/DailyRent/OutsideAlmaty → 422; ListingNotFound → 404; ChallengeBlocked/SourceUnavailable → 503 + Retry-After 5; PredictBusy/wait timeout → 503 + Retry-After 30; FileNotFoundError → 503; other ValueError/RuntimeError → 502.
6. `usage.record_event('predict')` ([[code/src/krisha/usage.py]]).

## Startup (`_lifespan` → `_startup`)
`_log_runtime_limits` → under flock: `pull_state` ([[code/src/krisha/subscriptions.py]]) → `db_release.ensure_db/ensure_models/ensure_rent_db/ensure_rent_models` ([[code/src/krisha/db_release.py]]) → `init_db` → `_warmup_runtime_caches` → `_refresh_live_pages` → background session warmup (krisha SafeLine) → background webhook setup.

## Inputs / outputs
- In: data/*.db, models/*, static/, env (PROXY_KEY, TRUSTED_PROXY_HOPS, RATE_LIMIT_*, WEB_CONCURRENCY, PREDICT_*, GA4_MEASUREMENT_ID, TELEGRAM_BOT_TOKEN…).
- Out: JSON per [[code/src/krisha/api/schemas.py]]; security headers (CSP, Permissions-Policy), X-Request-ID.

## Gotchas
- All caches/metrics/rate tables are per process.
- Exception subclass order in `predict` handler is load-bearing.
- Space restarts after each rescrape/retrain → in-memory state and logs live < 1 day.
- Tests: [[code/tests/_dir|tests/test_api_security.py]], [[code/tests/_dir|tests/test_api_load_hardening.py]], [[code/tests/_dir|tests/test_predict_gate.py]], e2e in tests/e2e ([[concepts/test-suite]]).
