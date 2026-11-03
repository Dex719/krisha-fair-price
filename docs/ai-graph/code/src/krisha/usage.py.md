---
type: file
path: src/krisha/usage.py
tags: [zone/src, layer/ops]
updated: 2026-11-03
---
# src/krisha/usage.py
purpose: Usage stats (site visits, predictions, bot messages) in data/usage_stats.json, flushed to GitHub at most every 30 min; weekly admin report.
concepts: [[concepts/state-storage]]
## Key symbols
- `FLUSH_INTERVAL` — 30 min; KEEP_DAYS 60; ALMATY_TZ UTC+5 `src/krisha/usage.py:33`
- `_hash_user` — salted sha256(chat_id)[:10] (issue #116) `src/krisha/usage.py:61`
- `record_event` — never raises; flush in daemon thread `src/krisha/usage.py:91`
- `merge_states` — max counter wins `src/krisha/usage.py:167`
- `send_weekly_report` — Mondays to TG_ADMIN_CHAT_ID `src/krisha/usage.py:238`
## Depends on
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/subscriptions.py]]
## Used by
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/daily_report.py]]
- [[code/scripts/send_alerts.py]]
- tests: [[code/tests/conftest.py]], [[code/tests/test_multiworker_safety.py]], [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_state_repo.py]], [[code/tests/_dir|tests/test_usage.py]]
## Data / env
- Env: STATE_ENCRYPTION_KEY or TELEGRAM_BOT_TOKEN (salt), TG_ADMIN_CHAT_ID, USAGE_FLUSH_SYNC.
## Gotchas
- Events since last flush are lost on restart (accepted).
