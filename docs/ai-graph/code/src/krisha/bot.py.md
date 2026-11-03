---
type: file
path: src/krisha/bot.py
tags: [zone/src, layer/bot]
updated: 2026-11-03
---
# src/krisha/bot.py
purpose: Telegram bot on the same FastAPI app via webhook POST /tg/webhook: URL evaluation, free-text evaluation, /track, /alerts, inline buttons, per-chat rate limits, webhook setup/self-healing, shared tg_call.
concepts: [[concepts/telegram-bot]]
## Key symbols
- `TG_API` — https://api.telegram.org; override via TG_API_BASE (proxy) `src/krisha/bot.py:59`
- `WEBHOOK_PATH` — /tg/webhook; allowed updates: message, callback_query `src/krisha/bot.py:65`
- `bot_token` — env TELEGRAM_BOT_TOKEN (absent → bot disabled) `src/krisha/bot.py:177`
- `webhook_secret` — sha256('kfp:'+token)[:32] = X-Telegram-Bot-Api-Secret-Token `src/krisha/bot.py:181`
- `_get_tg_client` — httpx client forced to IPv4 (HF Spaces IPv6 TLS hangs), retries=2 `src/krisha/bot.py:192`
- `tg_call` — POST Bot API method; never raises; token masked in logs `src/krisha/bot.py:242`
- `format_reply` — HTML answer for a prediction result `src/krisha/bot.py:351`
- `reply_keyboard` — buttons track:<id>, sim:<id>, site link `bagam.info/#check=<id>` `src/krisha/bot.py:461`
- `_BOT_LIMITS` — per-chat per-minute buckets: msg 12, text 3 (Gemini), state 5 (envs BOT_*_LIMIT_PER_MIN) `src/krisha/bot.py:543`
- `handle_update` — /start [payload], /help, /alerts*, /track, /untrack, URL → _evaluate_url, else free text `src/krisha/bot.py:672`
- `_evaluate_url` — predict_gate.cached_predict(live_vision=True) with mapped error messages `src/krisha/bot.py:723`
- `_handle_callback` — actions track/untrack/sim/redo/list/demo/help/alerts `src/krisha/bot.py:868`
- `_handle_start_payload` — deep links track_<id>, market_<slug> `src/krisha/bot.py:944`
- `_handle_track_command` — /track [url], /untrack <url>|all (max 10 per chat) `src/krisha/bot.py:1164`
- `setup_webhook` — register webhook at startup (background thread) `src/krisha/bot.py:1263`
- `webhook_status` — self-healing check from /api/health (hourly; retry 10 min) `src/krisha/bot.py:1309`
## Depends on
- [[code/src/krisha/analogs.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/_dir]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/predict_gate.py]]
- [[code/src/krisha/rent.py]]
- [[code/src/krisha/scraping/client.py]]
- [[code/src/krisha/scraping/detail_parser.py]]
- [[code/src/krisha/stats.py]]
- [[code/src/krisha/subscriptions.py]]
- [[code/src/krisha/text_parse.py]]
- [[code/src/krisha/tracking.py]]
- [[code/src/krisha/usage.py]]
## Used by
- [[code/src/krisha/alerts.py]]
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/channel.py]]
- [[code/src/krisha/daily_report.py]]
- [[code/src/krisha/monitoring.py]]
- [[code/src/krisha/report.py]]
- [[code/src/krisha/scraping/rescrape.py]]
- [[code/src/krisha/subscriptions.py]]
- [[code/src/krisha/usage.py]]
- [[code/scripts/send_alerts.py]]
- tests: [[code/tests/conftest.py]], [[code/tests/_dir|tests/test_almaty_only.py]], [[code/tests/_dir|tests/test_bot.py]], [[code/tests/_dir|tests/test_bot_audit_fixes.py]], [[code/tests/_dir|tests/test_bot_buttons.py]], [[code/tests/_dir|tests/test_bot_limits.py]], [[code/tests/_dir|tests/test_daily_report.py]], [[code/tests/_dir|tests/test_issue82_texts.py]], [[code/tests/_dir|tests/test_page_bot.py]], [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_predict_gate.py]], [[code/tests/_dir|tests/test_rent_serving.py]], [[code/tests/_dir|tests/test_send_alerts.py]], [[code/tests/_dir|tests/test_send_alerts_rent.py]], [[code/tests/_dir|tests/test_tracking.py]], [[code/tests/_dir|tests/test_vision.py]]
## Data / env
- Env: TELEGRAM_BOT_TOKEN, TG_API_BASE, TG_PROXY_SECRET (header X-Proxy-Secret to infra/tg-proxy-worker.js), SPACE_HOST, PUBLIC_BASE_URL, RAILWAY_PUBLIC_DOMAIN, BOT_RATE_LIMIT_PER_MIN, BOT_TEXT_LIMIT_PER_MIN, BOT_STATE_LIMIT_PER_MIN. Hosts: Telegram Bot API (or proxy), krisha.kz via predict.
## Gotchas
- Never send `str(exc)` to users (fixed); errors map to MSG_* texts; unexpected exceptions still answer the user.
- Webhook base: hosting domain (SPACE_HOST) preferred; PUBLIC_BASE_URL only without it.
- Rate limits checked BEFORE usage.record_event.
- Many modules import `tg_call` lazily from here (rescrape, monitoring, usage…) to avoid heavy imports.
