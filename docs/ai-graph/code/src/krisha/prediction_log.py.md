---
type: file
path: src/krisha/prediction_log.py
tags: [zone/src, layer/ops, layer/ml]
updated: 2026-11-03
---
# src/krisha/prediction_log.py
purpose: Durable prediction log (issue #128) in data/prediction_log.json via save_json_state, because SQLite predictions table is wiped on each redeploy.
concepts: [[concepts/state-storage]]
## Key symbols
- `LOG_PATH` — data/prediction_log.json, flat dict '<iso time>|<id>' → row `src/krisha/prediction_log.py:38`
- `MAX_ROWS` — 20000 `src/krisha/prediction_log.py:45`
- `_enabled` — only with PREDICTION_LOG=1; always off in GitHub Actions `src/krisha/prediction_log.py:52`
- `record` — never raises `src/krisha/prediction_log.py:80`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/subscriptions.py]]
## Used by
- [[code/src/krisha/db.py]]
- tests: [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_prediction_log.py]]
## Data / env
- Env PREDICTION_LOG, GITHUB_ACTIONS, USAGE_FLUSH_SYNC.
## Gotchas
- Each flush is a commit; enabling it without paths-ignore in deploy workflow would rebuild the Space every few minutes.
