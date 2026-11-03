---
type: workflow
path: .github/workflows/backfill-gap-cohort.yml
tags: [zone/ops, layer/ci, layer/data-repair]
updated: 2026-11-03
---
# .github/workflows/backfill-gap-cohort.yml ("Backfill gap cohort (разовый ремонт)")
purpose: one-off manual retro-labelling of the cohort of listings first seen during a recovery pass (incident 2026-07-26), predating the mechanism from #161. Dry-run by default.

## Trigger
`workflow_dispatch` only. Inputs: `deal` choice arenda|prodazha (default arenda), `window_start` (default `2026-07-26 16:04:00` UTC), `window_end` (exclusive, default `2026-07-26 18:01:00`), `expect` (expected lot count in window, default 5031 — guard against wrong DB), `apply` boolean (default false → `--dry-run`).
- `concurrency` group = `krisha-rent-db-writes` if deal==arenda else `krisha-db-writes` (fixed in audit 03.10; was always rent group).
- `permissions: contents: read`; 20 min; `KRISHA_DATA_REPO=Dex719/krisha-db`.

## Steps
1. Install `requirements.lock`.
2. Download `krisha_rent.db.gz` or `krisha.db.gz` + `.sha256` from `db-latest` via `gh release download`, `sha256sum -c`, gunzip to `data/`.
3. `python scripts/backfill_gap_cohort.py --db $DB --window-start --window-end --expect [--dry-run]` | tee → step summary. [[code/scripts/backfill_gap_cohort.py]]
4. If `apply == 'true'`: VACUUM, gzip, sha256, [[code/scripts/release_upload.py]] → `db-latest`.

## Secrets
`KRISHA_DB_TOKEN` (as `GH_TOKEN`).

## Health
Last runs 2026-07-26 (2 success, 1 failure). Dormant; keep only as repair template.

## Gotchas
- Inputs passed via env, never `${{ }}` in `run:` (shell-injection rule, audit 2026-10-06).
- Running with `apply` while a rescrape of the same deal is queued is serialized by the concurrency group; do not change group names without updating all three DB-writing workflows.

## Related
[[ops/workflows/rescrape]], [[ops/workflows/rescrape-rent]], [[concepts/state-storage]]
