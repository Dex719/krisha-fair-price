---
type: file
path: src/krisha/daily_report.py
tags: [zone/src, layer/ops, layer/bot]
updated: 2026-11-03
---
# src/krisha/daily_report.py
purpose: Daily admin report after rescrape (morning sale, evening rent) with OK/PROBLEM verdict from pipeline invariants, DB size, model metrics and usage.
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `SALE_SUMMARY_PATH` — /tmp/rescrape_stats.json (rescrape --summary-json); rent /tmp/rescrape_rent_stats.json `src/krisha/daily_report.py:42`
- `QUEUE_GROWTH_STREAK` — 3; CAP_HIT_STREAK 3; MAX_RETRAIN_AGE_DAYS 8; UNATTRIBUTED_ALERT_MIN 100 / SHARE 0.05 `src/krisha/daily_report.py:76`
- `_invariants` — 'pipeline alive' checks; shown only when failed `src/krisha/daily_report.py:117`
- `build_daily_report` — scope sale|rent `src/krisha/daily_report.py:282`
- `send_daily_report` — to TG_ADMIN_CHAT_ID `src/krisha/daily_report.py:394`
- `main` — `python -m krisha.daily_report --scope rent` `src/krisha/daily_report.py:419`
## Depends on
- [[code/src/krisha/_dir]]
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/monitoring.py]]
- [[code/src/krisha/usage.py]]
## Used by
- [[code/scripts/send_alerts.py]]
- [[ops/workflows/rescrape-rent]]
- tests: [[code/tests/_dir|tests/test_daily_report.py]], [[code/tests/_dir|tests/test_daily_report_verdict.py]], [[code/tests/_dir|tests/test_drain_mode.py]], [[code/tests/_dir|tests/test_rescrape_workflow.py]]
## Gotchas
- Strings from stats are HTML-escaped (parse_mode=HTML). Fail-soft: must not break the rescrape pipeline. The 14.07 breakage went unnoticed because the report needed mental comparison → verdict line first.
