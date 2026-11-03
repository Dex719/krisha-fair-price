---
type: ops
tags: [zone/ops, layer/config]
updated: 2026-11-03
---
# Environment variables and secrets
purpose: every env var from `.env.example` (all optional; missing → feature off, base mode = model estimate + site works) plus vars read in code but absent from `.env.example`, plus GitHub Actions secret names. Names only, never values.
Where set: prod = HF Space Settings (variables/secrets); CI = GitHub Actions secrets of Dex719/krisha-fair-price; local = `.env` (gitignored). Source file: [[code/dot-env.example]].

## Telegram bot
- `TELEGRAM_BOT_TOKEN` — @BotFather token; empty → bot off. Also fallback secret for state encryption key and chat_id hash salt. Read: `src/krisha/bot.py:177`, `src/krisha/subscriptions.py:80`, `src/krisha/usage.py:56`. [[concepts/telegram-bot]]
- `PUBLIC_BASE_URL` — public site URL (links, sitemap/robots, "Открыть приложение"). Prod = `https://bagam.info` (set in Space 2026-10-03). Webhook registers on `RAILWAY_PUBLIC_DOMAIN`/`SPACE_HOST` first, PUBLIC_BASE_URL only if neither. `src/krisha/api/app.py:1362`, `src/krisha/bot.py:1228`.
- `TG_API_BASE` — Bot API base (proxy, default `https://api.telegram.org`); used because HF Spaces often cannot reach api.telegram.org. Points to [[code/infra/tg-proxy-worker.js]]. `src/krisha/bot.py:63`.
- `TG_PROXY_SECRET` — sent as `X-Proxy-Secret` only when TG_API_BASE set; must equal worker secret `PROXY_SECRET`. Set in Space FIRST, then worker. `src/krisha/bot.py:235`.
- `BOT_RATE_LIMIT_PER_MIN` (default 12), `BOT_TEXT_LIMIT_PER_MIN` (3; free text → Gemini, cached 1 h), `BOT_STATE_LIMIT_PER_MIN` (5; /alerts_on, /alerts_off, /track, /untrack, deep-link start track_) — per chat, 60 s sliding window, per process (×WEB_CONCURRENCY). `src/krisha/bot.py:530`.
- `TG_ADMIN_CHAT_ID` — owner chat: weekly stats, retrain reports, failure alerts. `src/krisha/monitoring.py:23`.
- `TG_CHANNEL_ID` — channel for daily "Топ-5 выгодных лотов" digest (bot must be admin); used in Actions. `src/krisha/channel.py:24`, `src/krisha/report.py:21`.

## LLM (Gemini)
- `GEMINI_API_KEY` — Google AI Studio: description red flags, renovation from photos, estimate from pasted text; absent → degrade to cache. `src/krisha/llm_flags.py:28`, `vision.py`, `text_parse.py`.
- `GEMINI_MODEL` — default `gemini-2.5-flash-lite`. `src/krisha/llm_flags.py:30`.

## Private data repo / state ([[concepts/state-storage]])
- `KRISHA_DATA_REPO` — default `Dex719/krisha-db`; releases db-latest / model-latest / snapshot-* / model-YYYY-MM-DD. `src/krisha/db_release.py:59`. Workflows set it in `env:`.
- `KRISHA_STATE_REPO` — repo for bot state JSON (default same). `src/krisha/subscriptions.py:58`.
- `KRISHA_DB_TOKEN` — fine-grained PAT, Contents RW only on krisha-db. Without it DB/model/state don't load in prod. Legacy fallbacks in `subscriptions.py:316-322`: `GITHUB_PAT`, `GITHUB_TOKEN` (owner was told to delete `GITHUB_PAT` from Space).
- `KRISHA_STATE_PULL` — `0` = don't pull state at startup (tests/local). `src/krisha/api/app.py:1243`.
- `STATE_ENCRYPTION_KEY` — Fernet key secret for `subscriptions.json` / `tracked.json`; key = sha256("kfp-state:" + secret), secret = this var or TELEGRAM_BOT_TOKEN. Rotation order: (1) set STATE_ENCRYPTION_KEY = CURRENT bot token in Space AND Actions; (2) only then issue new token. Reverse order → state unreadable. After step 2 never remove it. Compromise of old token is not healed (no re-encryption tool). Actions consumers: `Alerts` step in [[ops/workflows/rescrape]], `Track alerts (аренда)` in [[ops/workflows/rescrape-rent]] ([[code/tests/_dir|tests/test_state_key_workflows.py]]). Status 2026-10-06: not yet set by owner.

## DB and models
- `KRISHA_DB_URL`, `KRISHA_MODEL_URL` — direct URLs instead of release (no token), local/tests. `src/krisha/db_release.py:97-110`.
- `KRISHA_DB_AUTO` — `0` = don't download DB at startup. `db_release.py:278,294`.
- (not in .env.example) `KRISHA_MODEL_AUTO` — `0` = don't download models. `db_release.py:352,366`.

## Network / rate limit
- `TRUSTED_PROXY_HOPS` — default 1 (HF router); client IP = Nth XFF element from the right; 0 = don't trust XFF. `src/krisha/api/app.py:641`.
- `PROXY_KEY` — shared secret with [[code/infra/domain-worker.js]]; app trusts `X-Bagam-Client-IP` only with matching `X-Bagam-Proxy-Key`. Empty → rate limit on bagam.info sees Cloudflare egress IP. `app.py:648`.
- `RATE_LIMIT_PER_WINDOW` (default 15), `RATE_LIMIT_WINDOW_S` (60) — per IP; raise if CGNAT users get 429 (restart enough). `app.py:597`.
- `DEMO_RATE_LIMIT_PER_WINDOW` — /api/demo limit (default 120 per same window), separate bucket. `app.py:607`.

## Caches / workers (load)
- `WEB_CONCURRENCY` — uvicorn workers, default 2 (Dockerfile ENV), ~600 MB each; 1 = old behaviour. `app.py:573`.
- `HEALTH_CACHE_TTL_S` (60) — /api/health cache. `app.py:337`.
- `PREDICT_CACHE_TTL_S` (600) — parsed listing cache. `src/krisha/predict_gate.py:63`.
- `DEMO_POOL_TTL_S` (600) — "Показать на примере" pool refresh. `app.py:894`.
- `PREDICT_NEGATIVE_TTL_S` (60) — negative (broken/removed link) cache.
- `PREDICT_SLOTS` (10), `PREDICT_SLOT_WAIT_S` (15) — concurrent krisha.kz fetches per process; then 503 + Retry-After. `predict_gate.py:73`.
- `PREDICT_WAIT_S` (20) — total user-path budget before 503. `app.py:766`.
- `USER_SCRAPE_TIMEOUT_S` (5), `USER_SCRAPE_CONNECT_S` (3) — per-request network budget on user path. `predict_gate.py:80`.

## Scraping
- `KRISHA_DELAY_MIN`, `KRISHA_DELAY_MAX` — polite delay range s (default 2.0–4.0; empty string = "use mode preset" drain 1.5–3.0 / steady 2.0–4.0). `src/krisha/config.py:131`, `src/krisha/scraping/rescrape.py:461`. [[concepts/scraping-krisha]]

## Read in code but NOT in .env.example
- `SPACE_HOST` (set by HF), `RAILWAY_PUBLIC_DOMAIN` (Railway) — webhook base. `bot.py:1232`.
- `BUILD_REVISION` — overrides `data/build_revision.txt` for `/api/health.revision`. `app.py:350`.
- `BOT_UPDATE_CONCURRENCY` (4) — concurrent Telegram update handlers. `app.py:1076`.
- `KRISHA_SESSION_WARMUP` (`0` disables krisha session warmup thread). `app.py:1193`.
- `KRISHA_LIVE_PAGES` (`0` disables live numbers injection into static pages). `app.py:1475`.
- `SOURCE_FAILURE_TTL_S` (4.0) — memory of source failures. `predict_gate.py:72`.
- `PREDICTION_LOG` (`auto`; off under `GITHUB_ACTIONS=true`). `src/krisha/prediction_log.py:65`.
- `USAGE_FLUSH_SYNC=1` — synchronous flush (tests/CLI). `usage.py:144`.
- `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, `NUMEXPR_NUM_THREADS` = 1 (Dockerfile). `PYTHONUNBUFFERED=1`.
- Scripts: `PROD_BASE_URL`, `SMOKE_TIMEOUT_S` ([[code/scripts/smoke_prod.py]]), `HEALTH_URL` ([[code/scripts/health_check.py]]), `LLM_BATCH_DELAY` (scripts/analyze_flags.py, default 7).

## GitHub Actions secrets (repo Dex719/krisha-fair-price)
| secret | used by |
|---|---|
| `KRISHA_DB_TOKEN` | rescrape, rescrape-rent, retrain, restore-drill, backfill-gap-cohort, ci (optional) |
| `HF_TOKEN` | deploy-hf (push), rescrape/rescrape-rent/retrain/space-keepalive (restart API) |
| `TELEGRAM_BOT_TOKEN` | rescrape, rescrape-rent, retrain, smoke, audit, restore-drill, space-keepalive |
| `TG_ADMIN_CHAT_ID` | same as above |
| `TG_CHANNEL_ID` | rescrape (Alerts) |
| `STATE_ENCRYPTION_KEY` | rescrape (Alerts), rescrape-rent (Track alerts) |
| `GITHUB_TOKEN` (auto) | deploy-hf gate, retrain push |
Repo variable: `PROD_BASE_URL` (smoke). Dependabot secrets also hold `KRISHA_DB_TOKEN` (memory 2026-10-01).

## Cloudflare worker secrets/vars (set in Cloudflare UI, not repo)
`PROXY_KEY` (domain worker = Space PROXY_KEY), `PROXY_SECRET` (tg worker = Space TG_PROXY_SECRET), `BOT_ID` (tg worker, numeric part of token — public). See [[ops/infra]].
