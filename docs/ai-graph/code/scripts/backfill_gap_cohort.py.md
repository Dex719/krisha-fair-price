---
type: file
path: scripts/backfill_gap_cohort.py
tags: [zone/scripts, layer/data-repair]
updated: 2026-11-03
---
# scripts/backfill_gap_cohort.py
purpose: one-off retro-labelling (issue #156) of the recovery-pass cohort in the RENT DB: pass of 2026-07-26 16:04–18:00 UTC gave 5031 lots `first_seen=26.07` without a cohort label (#161 labelling mechanism merged 17:58, after the pass started).
## Logic
- `gap_start` = `MAX(last_seen)` before the window (derived from DB); cohort label `gap:<gap_start date>`
- `INSERT OR IGNORE INTO data_gaps (gap_start, gap_end, note)`; `UPDATE listings SET first_seen_cohort=? ... AND first_seen_cohort IS NULL` → idempotent
- deliberately NOT an auto-rule in `_migrate()`: the same signature exists in the SALE DB (gap 13.06–01.07, 1919 delistings on 02.07, lag ≈21 d) where the backfill smeared over 12 days due to `--max-new 1000/day` and must NOT be labelled
## CLI
- `--db` (required), `--window-start`, `--window-end` (exclusive; both required), `--expect N` (sanity count), `--dry-run`
## Depends on
- [[code/src/krisha/db.py]] (`init_db`)
## Used by
- [[ops/workflows/backfill-gap-cohort]] (workflow_dispatch only), followed by [[code/scripts/release_upload.py]] to `db-latest`
## Data / env
- `data/krisha_rent.db` → [[concepts/state-storage]]
