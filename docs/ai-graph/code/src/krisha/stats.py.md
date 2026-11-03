---
type: file
path: src/krisha/stats.py
tags: [zone/src, layer/data]
updated: 2026-11-03
---
# src/krisha/stats.py
purpose: Market statistics for /api/stats, /api/stats/rent, /api/heatmap and reports; falls back to models/stats.json snapshot when DB is absent.
concepts: [[concepts/web-api]]
## Key symbols
- `STATS_SNAPSHOT_PATH` — models/stats.json `src/krisha/stats.py:18`
- `_weekly_trend` — weekly median ₸/m² of listings active that week (also used by forecast/report) `src/krisha/stats.py:64`
- `data_updated_at` — updated_at = collector freshness (db.data_observed_at) `src/krisha/stats.py:130`
- `compute_stats` — sale stats; raises FileNotFoundError if no DB `src/krisha/stats.py:149`
- `compute_rent_stats` — rent medians by district/rooms, distribution, ₸/m² trend, district yields `src/krisha/stats.py:219`
- `snapshot_stats` — write models/stats.json (called by train for sale) `src/krisha/stats.py:285`
- `heatmap_points` — ~400 m grid of ₸/m² `src/krisha/stats.py:296`
- `get_stats` — live DB else snapshot `src/krisha/stats.py:324`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
## Used by
- [[code/src/krisha/alerts.py]]
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/channel.py]]
- [[code/src/krisha/factor_hints.py]]
- [[code/src/krisha/forecast.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/report.py]]
- [[code/src/krisha/subscriptions.py]]
- [[code/src/krisha/text_parse.py]]
- [[code/src/krisha/train.py]]
- [[code/scripts/restore_drill.py]]
- tests: [[code/tests/_dir|tests/test_alerts.py]], [[code/tests/_dir|tests/test_stats.py]]
## Gotchas
- Uses direct sqlite3.connect (allowlisted, not on predict hot path).
