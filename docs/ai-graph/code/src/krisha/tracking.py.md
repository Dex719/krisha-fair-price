---
type: file
path: src/krisha/tracking.py
tags: [zone/src, layer/bot]
updated: 2026-11-03
---
# src/krisha/tracking.py
purpose: /track: per-chat watched listings; after each rescrape compare with DB and emit price-change / delisted messages (sale and rent separately).
concepts: [[concepts/telegram-bot]]
## Key symbols
- `TRACKED_PATH` — data/tracked.json {chat_id: {listing_id: {price, title, since, deal?}}} `src/krisha/tracking.py:33`
- `MAX_TRACKED_PER_CHAT` — 10 `src/krisha/tracking.py:34`
- `lot_deal` — missing 'deal' = sale `src/krisha/tracking.py:38`
- `add_tracked` — (ok, reason); refreshes from server first `src/krisha/tracking.py:115`
- `remove_tracked` — one or all `src/krisha/tracking.py:153`
- `check_tracked_updates` — → [(chat_id, html)]; only_chats = persist only delivered chats `src/krisha/tracking.py:205`
- `_lot_event` — price change or delist (trusted lag ≤ MAX_TRUSTED_DELIST_LAG_DAYS) `src/krisha/tracking.py:295`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/_dir]]
- [[code/src/krisha/subscriptions.py]]
## Used by
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/subscriptions.py]]
- [[code/scripts/send_alerts.py]]
- tests: [[code/tests/_dir|tests/test_almaty_only.py]], [[code/tests/_dir|tests/test_bot.py]], [[code/tests/_dir|tests/test_bot_buttons.py]], [[code/tests/_dir|tests/test_bot_state_merge.py]], [[code/tests/_dir|tests/test_page_bot.py]], [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_state_encryption.py]], [[code/tests/_dir|tests/test_tracking.py]]
## Gotchas
- Persist state only after successful delivery (otherwise user never learns about the change).
