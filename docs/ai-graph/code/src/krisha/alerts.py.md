---
type: file
path: src/krisha/alerts.py
tags: [zone/src, layer/bot]
updated: 2026-11-03
---
# src/krisha/alerts.py
purpose: Daily alerts after rescrape: new listings (window 26 h) priced in batch; GOOD_DEAL ones sent to subscribers matching filters, with delivery log and pacing.
concepts: [[concepts/telegram-bot]]
## Key symbols
- `ALERT_WINDOW_HOURS` — 26 `src/krisha/alerts.py:26`
- `MAX_DEALS_PER_CHAT` — 5 `src/krisha/alerts.py:27`
- `ALERTED_PATH` — data/alerted_ids.json (dedup; keep 2000) `src/krisha/alerts.py:40`
- `new_listings` — active, price>0, area>0, first_seen in window OR scraped_at in window and first_seen ≤ FRESH_DAYS `src/krisha/alerts.py:106`
- `find_good_deals` — batch pricing (ALERT_BATCH_SIZE=500) + log_predictions; suspicious deals sorted last `src/krisha/alerts.py:130`
- `AlertsPricingFailed` — window non-empty but nothing priced → error, not silence `src/krisha/alerts.py:170`
- `deliver` — sendMessage with one retry on 429 (retry_after ≤ 30 s) `src/krisha/alerts.py:320`
- `_Pacer` — ≥1.1 s per chat, 0.04 s global `src/krisha/alerts.py:285`
- `send_alerts` — header + one card per deal with buttons `src/krisha/alerts.py:441`
## Depends on
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/market.py]]
- [[code/src/krisha/_dir]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/scam.py]]
- [[code/src/krisha/stats.py]]
- [[code/src/krisha/subscriptions.py]]
## Used by
- [[code/scripts/send_alerts.py]]
- tests: [[code/tests/_dir|tests/test_alerts.py]], [[code/tests/_dir|tests/test_alerts_batch.py]], [[code/tests/_dir|tests/test_alerts_delivery.py]], [[code/tests/_dir|tests/test_bot_buttons.py]], [[code/tests/_dir|tests/test_page_bot.py]]
## Gotchas
- 01–03.10: models moved to private release, job didn't download them, alerts silently empty → now raises AlertsPricingFailed.
- Per-lot full cards took 80 min on 4–7k lots and blocked DB upload (2026-09-20) → batch pricing.
- Gone chats (blocked/deleted) are forgotten; group→supergroup migration moves subscription.
