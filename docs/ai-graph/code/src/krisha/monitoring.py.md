---
type: file
path: src/krisha/monitoring.py
tags: [zone/src, layer/ops, layer/ml]
updated: 2026-11-03
---
# src/krisha/monitoring.py
purpose: Model monitoring: metrics_history.jsonl append/load, dataset summary, Telegram retrain report.
concepts: [[concepts/sale-price-model]]
## Key symbols
- `ADMIN_CHAT_ENV` — 'TG_ADMIN_CHAT_ID' (imported by rescrape, report, daily_report) `src/krisha/monitoring.py:23`
- `append_metrics_history` — one line per retrain `src/krisha/monitoring.py:26`
- `format_retrain_report` — metrics + deltas vs gate baseline + verdict `src/krisha/monitoring.py:138`
- `notify_retrain` — send via tg_call `src/krisha/monitoring.py:186`
## Depends on
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
## Used by
- [[code/src/krisha/daily_report.py]]
- [[code/src/krisha/report.py]]
- [[code/src/krisha/scraping/rescrape.py]]
- [[code/src/krisha/train.py]]
- [[code/scripts/notify_retrain.py]]
- tests: [[code/tests/_dir|tests/test_monitoring.py]], [[code/tests/_dir|tests/test_rent_model.py]]
## Data / env
- models/metrics_history.jsonl, models/rent/metrics_history.jsonl.
