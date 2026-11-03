---
type: file
path: scripts/send_alerts.py
tags: [zone/scripts, layer/bot, layer/ops]
updated: 2026-11-03
---
# scripts/send_alerts.py
purpose: post-rescrape Telegram fan-out: good-deal alerts to subscribers, `/track` watch updates, channel digest «Топ-5 лотов дня», monthly report, weekly usage report, daily admin report. See [[concepts/telegram-bot]].
## Order in `main()` :150 (short one-offs before the long deal fan-out)
1. `find_good_deals()` (batch pricing; `AlertsPricingFailed` → deals skipped, rest still sent, exit 1)
2. `_send_track_alerts` :66 → 3. `_post_channel_digest` :104 → 4. `_maybe_monthly_report` :114 → 5. `_maybe_weekly_usage_report` :130 → 6. `_send_daily_admin_report` :142 → 7. `_send_deal_alerts` :31
- `--deal arenda` → only rent `/track` updates on `RENT_DB_PATH`, return 0
- SIGTERM → `sys.exit(143)` so `finally` blocks flush the delivery journal on Actions step timeout
- `_quiet_hours` :56 (Almaty TZ from `krisha.usage.ALMATY_TZ`)
## CLI
- `--dry-run`, `--deal prodazha|arenda` (default prodazha)
## Depends on
- [[code/src/krisha/alerts.py]] (`find_good_deals`, `format_alert`, `match_filters`, `send_alerts`, `_Pacer`, `deliver`, `AlertsPricingFailed`), [[code/src/krisha/subscriptions.py]] (`load_subscriptions`, `_alert_admin`), [[code/src/krisha/tracking.py]] (`check_tracked_updates`), [[code/src/krisha/bot.py]] (`mask_chat_id`), [[code/src/krisha/config.py]], [[code/src/krisha/channel.py]], [[code/src/krisha/report.py]], [[code/src/krisha/usage.py]], [[code/src/krisha/daily_report.py]]
## Used by
- [[ops/workflows/rescrape]] — step "Alerts" (timeout 15, continue-on-error, after DB upload/restart/snapshot), then "Upload DB again" (predictions written by alerts)
- [[ops/workflows/rescrape-rent]] — `send_alerts.py --deal arenda`
- tests `tests/test_send_alerts.py`, `tests/test_send_alerts_rent.py`
## Data / env
- env `TELEGRAM_BOT_TOKEN`, `KRISHA_DB_TOKEN` (subscriber/track state in private repo, [[concepts/state-storage]]), optional `TG_CHANNEL_ID`
- models must be in `models/` (Actions: `python -m krisha.db_release --models`)
## Gotchas
- 2026-09-20: alerts step ran per-lot full predict (~0.5–0.75 s/lot, 4–7k lots) and starved the DB upload → batch pricing + step reorder (spec rescrape-post-steps, [[history/kiro-specs]])
