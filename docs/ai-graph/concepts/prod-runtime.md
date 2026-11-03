---
type: concept
tags: [zone/ops, layer/runtime]
updated: 2026-11-03
---
# Prod runtime (HF Space + bagam.info + Cloudflare workers)
purpose: what production physically is, its limits, lifecycle and observability.

## Shape
- One Docker container on Hugging Face Spaces free tier (cpu-basic ≈ 2 vCPU, 16 GB RAM). Space `Dex719/krisha-fair-price`, origin `https://dex719-krisha-fair-price.hf.space`, port 7860. Image: [[ops/build]].
- `uvicorn krisha.api.app:app --workers ${WEB_CONCURRENCY:-2}`; ~600 MB per worker; BLAS/OMP threads = 1. App: [[concepts/web-api]], [[code/src/krisha/api/app.py]].
- In front: HF router (1 trusted proxy hop → `TRUSTED_PROXY_HOPS=1`; no compression/caching), and for the public domain `bagam.info` a Cloudflare Worker ([[code/infra/domain-worker.js]]) with 120 s edge cache for pages/static.
- Telegram: webhook to hf.space; outbound Bot API through tg-proxy worker ([[code/infra/tg-proxy-worker.js]]) when `TG_API_BASE` set. [[concepts/telegram-bot]]

## Lifecycle
- Boot (`_startup`): log CPU quota → under `fcntl.flock(data/.startup.lock)`: pull bot state, download DB + models (sale & rent) from private releases ([[concepts/state-storage]]), `init_db` migrations → warm caches (CatBoost load, spatial KD-trees) → optional krisha session warmup thread.
- Restarts: after every rescrape (sale ~daily morning, rent ~daily evening), after retrain, on deploy, on manual keepalive restart. ⇒ container uptime < 24 h; in-memory `/api/metrics` and logs do not persist; no history of predict errors.
- Multi-worker coordination: startup flock (one download/migration), `tg_updates` table dedups Telegram updates, usage stats merged on flush, per-process rate limit / predict slots (effective limits ×2).
- Docker HEALTHCHECK `/api/health` every 60 s, timeout 35 s, 3 retries.

## Observability
- `/api/health` (cached `HEALTH_CACHE_TTL_S`=60): revision (= deployed GitHub SHA from `data/build_revision.txt`), model_loaded, data freshness, webhook status.
- [[ops/workflows/space-keepalive]] (6-hourly health + alerts on state change), [[ops/workflows/smoke]] (post-deploy + 2×/day), Telegram admin chat.
- HF logs only for logged-in owner (API 401).

## Limits / policies
- Do not load-test prod (HF bans IP). Realistic perf only via `docker run --cpus=2` locally.
- User predict path budgets: `PREDICT_SLOTS`=10/process, `PREDICT_WAIT_S`=20, `USER_SCRAPE_TIMEOUT_S`=5 → 503 + Retry-After instead of hanging. Source failures (krisha.kz SafeLine 403/blocks) surface as 503 "Источник временно не отдаёт объявление" — seen in smoke 2026-11-02..03. [[concepts/scraping-krisha]]
- Rate limit per client IP 15/60 s (demo 120); on bagam.info correct only if `PROXY_KEY` set on worker and Space.
- Real traffic tiny (memory 2026-09: 1–3 visits/day).

## Owner manual steps
Tracked outside the repo (owner's local agent memory); ask the owner before assuming any Cloudflare/secret setting. Domain chain: Northwest registrar → Cloudflare NS → Worker `bagam-proxy` → Space.

## Related
[[ops/deploy]], [[ops/infra]], [[ops/env-vars]], [[concepts/ci-pipeline]]
