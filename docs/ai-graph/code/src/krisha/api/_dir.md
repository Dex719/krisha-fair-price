---
type: dir
path: src/krisha/api
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# src/krisha/api
purpose: HTTP layer: one FastAPI app serving the static site (from memory), JSON API, SEO endpoints and the Telegram webhook. `__init__.py` is empty. Concept: [[concepts/web-api]].
## Files
- [[code/src/krisha/api/app.py]] — app, routes, middlewares, rate limit, startup.
- [[code/src/krisha/api/schemas.py]] — Pydantic contracts.
- [[code/src/krisha/api/cache.py]] — TTLCache (single-flight, SWR) also used by predict_gate and text_parse.
- [[code/src/krisha/api/metrics.py]] — per-process counters for /api/metrics.
- [[code/src/krisha/api/static_cache.py]] — precompressed static assets, ?v= hashing.
- [[code/src/krisha/api/live_pages.py]] — live numbers / JSON-LD / llms.txt injected into HTML.
- [[code/src/krisha/api/site_analytics.py]] — GA4 + verification metas from env, CSP extension.
## Main data flow
Request → `_security_headers` http-middleware (outermost, added last: Content-Length > 64 KiB → 413, X-Request-ID, X-Robots-Tag noindex for /api,/tg…, metrics.observe, CSP/security headers) → `_GZipExceptCompressed` → `_ChunkedBodyLimitMiddleware` (64 KiB for chunked bodies) → route. `/api/predict`: `predict_gate.peek` → `_check_rate_limit` → thread (`_PREDICT_LIMITER`) → `predict_gate.cached_predict` → [[code/src/krisha/predict.py]]. `/tg/webhook` → background `bot.handle_update` ([[code/src/krisha/bot.py]]). Pages: `_render_pages` at startup with [[code/src/krisha/api/live_pages.py]] + [[code/src/krisha/api/static_cache.py]] from `static/` ([[concepts/frontend]]).
