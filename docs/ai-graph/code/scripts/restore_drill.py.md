---
type: file
path: scripts/restore_drill.py
tags: [zone/scripts, layer/ops]
updated: 2026-11-03
---
# scripts/restore_drill.py
purpose: backup restore drill — proves the service can be brought up from the published `db-latest` DB. Works in a temp dir, changes nothing in the project.
## Steps
1. download `krisha.db.gz` (`--rent`: `krisha_rent.db.gz`) via `krisha.db_release.download(..., strict_checksum=True)` — missing `.sha256` = FAIL (audit 24.10; previously silently skipped)
2. gunzip + `PRAGMA integrity_check`
3. schema/data sanity: `REQUIRED_TABLES = ("listings", "price_history", "sweep_runs")` :44, non-empty listings/price history, active lots > 0
4. freshness like prod: `db.data_observed_at` (end of last pass in `sweep_runs`, not `MAX(last_seen)` which user checks also move) ≤ `--max-age-hours` (48)
5. start app on that DB (`fastapi.testclient.TestClient`, `KRISHA_DB_AUTO=0`) and hit `/api/health`, `/api/stats`; rent: `krisha.stats.compute_rent_stats` (= `/api/stats/rent`)
## Key symbols
- `restore` :58, `_observed_at` :71, `check_database` :79, `check_app` :123, `check_rent_app` :147, `main` :156
## CLI
- `--max-age-hours 48`, `--rent`, `--skip-app`, `--keep PATH` (keep restored DB)
## Depends on
- [[code/src/krisha/db_release.py]], [[code/src/krisha/db.py]] (`data_observed_at`), [[code/src/krisha/api/app.py]], [[code/src/krisha/stats.py]]
## Used by
- [[ops/workflows/restore-drill]] (cron `0 10 * * 3` = Wed 15:00 Almaty): `restore_drill.py` then `restore_drill.py --rent`
## Data / env
- needs `KRISHA_DB_TOKEN` for private repo → [[concepts/state-storage]]
## Gotchas
- existed but never ran until audit 24.10 added the workflow
