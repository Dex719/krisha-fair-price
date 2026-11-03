---
type: workflow
path: .github/workflows/restore-drill.yml
tags: [zone/ops, layer/ci, layer/backup]
updated: 2026-11-03
---
# .github/workflows/restore-drill.yml ("Restore drill")
purpose: weekly proof that the only data copy (`db-latest` in private krisha-db) can be restored and served: download strictly, integrity/schema/freshness checks, boot app on it.

## Trigger
`cron "0 10 * * 3"` = Wednesday 10:00 UTC = 15:00 Almaty (between sale and rent passes); `workflow_dispatch`. `concurrency: restore-drill` (read-only, no lock with rescrapes). 30 min. `permissions: contents: read`. `KRISHA_DATA_REPO=Dex719/krisha-db`.

## Steps
1. Install `requirements.lock` + `-e . --no-deps`.
2. `python -m krisha.db_release --models --require` (health checks `model_loaded`).
3. `Drill (продажа)` (continue-on-error) — `python scripts/restore_drill.py`.
4. `Drill (аренда)` (continue-on-error) — `python scripts/restore_drill.py --rent`.
5. `Verdict` — summary line; fails unless both success.
6. Telegram on failure (admin chat).
restore_drill.py: strict sha256 (missing .sha256 = fail), `PRAGMA integrity_check`, required tables non-empty, freshness via `db.data_observed_at` ≤ 48 h (`--max-age-hours`), TestClient `/api/health` + `/api/stats` (rent: `/api/stats/rent`); all in temp dir. [[code/scripts/restore_drill.py]]

## Secrets
`KRISHA_DB_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TG_ADMIN_CHAT_ID`.

## Health
Only one scheduled run so far: 2026-10-28 success.

## Gotchas
- Added audit 24.10: script existed but was never run ("a backup never restored is not a backup").
- Reader tolerates the `.uploading` rename window of [[code/scripts/release_upload.py]].

## Related
[[concepts/state-storage]], [[concepts/ci-pipeline]]
