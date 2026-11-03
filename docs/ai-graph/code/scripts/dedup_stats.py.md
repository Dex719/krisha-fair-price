---
type: file
path: scripts/dedup_stats.py
tags: [zone/scripts, layer/ml]
updated: 2026-11-03
---
# scripts/dedup_stats.py
purpose: issue #152 — print fingerprint dedup statistics on current DB without training (train pipeline up to dedup: load_dataset → clean → resolve_zones → dedup_relistings). Writes NOTHING.
## CLI
- `--db` (default `DB_PATH`, sale DB)
## Depends on
- [[code/src/krisha/config.py]], [[code/src/krisha/features.py]] (`clean`), [[code/src/krisha/train.py]] (`dedup_relistings`, `load_dataset`), [[code/src/krisha/zones.py]]
## Used by
- manual; tests `tests/test_dedup_stats.py`
## Gotchas
- dup share depends on coverage: 10.51% on 26 322 lots (02.08); current sale meta shows `dedup.dropped_pct=22.25` (132 435 → 102 970 rows, `models/model_meta.json`). Same numbers are logged by every retrain.
