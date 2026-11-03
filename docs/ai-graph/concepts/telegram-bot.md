---
type: concept
tags: [zone/src, layer/bot]
updated: 2026-11-03
---
# Telegram bot, alerts, tracking, channel
purpose: Telegram side of baǵam: interactive bot (webhook in the web app) plus batch notifications sent from GitHub Actions after each rescrape.

## Interactive bot ([[code/src/krisha/bot.py]])
1. Telegram → POST /tg/webhook ([[code/src/krisha/api/app.py]] `telegram_webhook`): secret header = `webhook_secret(token)`, update_id dedup (in-process deque + `db.remember_update_id`), processing in background (`BOT_UPDATE_CONCURRENCY` 4).
2. `handle_update`: per-chat rate buckets (msg 12/min, text 3/min, state 5/min) → `usage.record_event('bot')` → commands: `/start [track_<id>|market_<slug>]`, `/help`, `/alerts`, `/alerts_on <filters>`, `/alerts_off`, `/track [url]`, `/untrack <url>|all`; krisha URL → `_evaluate_url`; other text ≥ 40 chars → Gemini parse ([[code/src/krisha/text_parse.py]]).
3. `_evaluate_url` → [[code/src/krisha/predict_gate.py]] `cached_predict(live_vision=True)` → `format_reply` (photo + caption ≤1024) + buttons `track:<id>`, `sim:<id>`, site link.
4. Callbacks: track/untrack/sim/redo/list/demo/help/alerts.
5. Outbound: `tg_call` (IPv4-forced httpx, optional proxy `TG_API_BASE` + `TG_PROXY_SECRET` for infra/tg-proxy-worker.js).

## Batch notifications ([[code/scripts/send_alerts.py]], after [[ops/workflows/rescrape]]; rent run uses `--deal arenda` = tracking only)
Order: `find_good_deals` ([[code/src/krisha/alerts.py]], batch pricing of lots new in 26 h) → track alerts ([[code/src/krisha/tracking.py]] `check_tracked_updates`) → channel digest ([[code/src/krisha/channel.py]], `TG_CHANNEL_ID`) → monthly report on the 1st ([[code/src/krisha/report.py]]) → weekly usage report on Mondays ([[code/src/krisha/usage.py]]) → daily admin report ([[code/src/krisha/daily_report.py]]) → subscriber deal alerts (`alerts.send_alerts`, ≤5 deals/chat, paced). If pricing failed (`AlertsPricingFailed`): skip deals+digest, alert admin, exit 1.
Also admin messages: retrain report ([[code/src/krisha/monitoring.py]]), ban alerts ([[code/src/krisha/scraping/rescrape.py]]), evening rent report (`python -m krisha.daily_report --scope rent`).

## State
JSON files in data/ persisted to private repo via [[code/src/krisha/subscriptions.py]] `save_json_state` (Fernet-encrypted; merge-on-write; fail-closed): subscriptions.json, tracked.json, alerted_ids.json, alerted_chats.json, channel_posted.json, usage_stats.json, prediction_log.json — see [[concepts/state-storage]].

## Key thresholds
ALERT_WINDOW_HOURS 26 · MAX_DEALS_PER_CHAT 5 · MAX_TRACKED_PER_CHAT 10 · DIGEST_SIZE 5 · per-chat pause 1.1 s, global 0.04 s · 429 retry_after ≤ 30 s · usage flush 30 min.

## Gotchas
- Without `TELEGRAM_BOT_TOKEN` the bot and all tg_call are no-ops (app still works).
- Track state is saved only for chats whose message was delivered (`only_chats`).
- Bot replies never include raw exception text; source failures get «krisha сейчас не отдаёт…».
- Webhook registration runs in a background thread; `/api/health` re-checks hourly and re-registers.
- User-facing wording avoids jargon («объявление», not «лот»); FAIR = «В рынке».
