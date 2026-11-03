---
type: file
path: src/krisha/report.py
tags: [zone/src, layer/bot]
updated: 2026-11-03
---
# src/krisha/report.py
purpose: Monthly market report (1st of month) to admin and channel from compute_stats.
concepts: [[concepts/telegram-bot]]
## Key symbols
- `build_monthly_report` — HTML from stats `src/krisha/report.py:48`
- `send_monthly_report` — to TG_ADMIN_CHAT_ID and TG_CHANNEL_ID `src/krisha/report.py:86`
## Depends on
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/monitoring.py]]
- [[code/src/krisha/stats.py]]
## Used by
- [[code/scripts/send_alerts.py]]
- tests: [[code/tests/_dir|tests/test_report.py]]
