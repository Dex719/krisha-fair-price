---
type: file
path: scripts/notify_retrain.py
tags: [zone/scripts, layer/ml, layer/ops]
updated: 2026-11-03
---
# scripts/notify_retrain.py
purpose: Telegram report to admin after weekly retrain (old vs new metrics, gate outcome, dataset summary, metrics history).
## CLI
- positional `old_meta new_meta`; `--gate success|failure` (default success); `--deal prodazha|arenda`
## Depends on
- [[code/src/krisha/model_spec.py]] (`SPECS`, `spec_for`)
- [[code/src/krisha/monitoring.py]] (`dataset_summary`, `format_retrain_report`, `load_metrics_history`, `notify_retrain`)
- [[concepts/telegram-bot]]
## Used by
- [[ops/workflows/retrain]] — `if: always()` step after gate: `notify_retrain.py /tmp/old_meta.json models/model_meta.json --gate ${{ steps.gate.outcome }}` (rent variant with `models/rent/model_meta.json`)
## Data / env
- env `TELEGRAM_BOT_TOKEN`, `TG_ADMIN_CHAT_ID`; absent → prints report to stdout, does not fail
