---
type: file
path: src/krisha/channel.py
tags: [zone/src, layer/bot]
updated: 2026-11-03
---
# src/krisha/channel.py
purpose: Public Telegram channel digest «Топ-5 выгодных» after nightly rescrape.
concepts: [[concepts/telegram-bot]]
## Key symbols
- `CHANNEL_ENV` — TG_CHANNEL_ID `src/krisha/channel.py:24`
- `POSTED_PATH` — data/channel_posted.json (keep 500) `src/krisha/channel.py:25`
- `post_channel_digest` — picks unposted deals, posts, persists ids `src/krisha/channel.py:58`
## Depends on
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/_dir]]
- [[code/src/krisha/stats.py]]
- [[code/src/krisha/subscriptions.py]]
## Used by
- [[code/scripts/send_alerts.py]]
- tests: [[code/tests/_dir|tests/test_channel.py]]
