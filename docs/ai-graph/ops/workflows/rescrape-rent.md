---
type: workflow
path: .github/workflows/rescrape-rent.yml
tags: [zone/ops, layer/ci, layer/scraping, deal/arenda]
updated: 2026-11-03
---
# .github/workflows/rescrape-rent.yml ("Daily rent rescrape")
purpose: nightly scrape of krisha.kz RENT listings (`--deal arenda`) → `data/krisha_rent.db` → release `db-latest` → restart Space → rent /track alerts + evening admin report.

## Trigger
- `cron "0 15 * * *"` = 15:00 UTC = 20:00 Almaty; evening so it does not overlap the sale pass (IP ban). Observed start 17:58–23:01 UTC.
- `workflow_dispatch`: `pages` (250), `max_new` (1000), `bootstrap` (boolean, false; start from empty DB only if asset absent).
- `concurrency: krisha-rent-db-writes` (shared with [[ops/workflows/backfill-gap-cohort]] deal=arenda). NOT shared with sale group.
- `permissions: contents: read`; `KRISHA_DATA_REPO=Dex719/krisha-db`; timeout 350 min.

## Steps
1. `Download rent DB from release` — if `bootstrap=true` and no `krisha_rent.db.gz(.uploading)` asset in `db-latest` → exit 0 (empty DB). Else `python -m krisha.db_release --rent --require --force --strict-checksum` (missing asset / bad gzip / missing or mismatched .sha256 = error). [[code/src/krisha/db_release.py]]
2. `Restore sweep state` — `sweep_state_file pull --deal arenda`. [[code/src/krisha/scraping/sweep_state_file.py]]
3. `Rescrape rent` (315 min) — `KRISHA_DELAY_MIN=2.5 KRISHA_DELAY_MAX=5.0` fixed; `scripts/rescrape.py --deal arenda --pages ${INPUT_PAGES:-250} --max-new ${INPUT_MAX_NEW:-1000} --time-budget-min 300 --fail-empty --fail-below 2000 --summary-json /tmp/rescrape_rent_stats.json`. [[code/scripts/rescrape.py]]
4. `Job summary` (always).
5. `Save sweep state` (always, continue-on-error) → `sweep_state_arenda.json` into `db-latest`.
6. `Upload rent DB to release` (15 min) — VACUUM, gzip, sha256, [[code/scripts/release_upload.py]] → `krisha_rent.db.gz` (+.sha256).
7. `Restart Space` (continue-on-error) — HF restart API (prod uses rent DB for rent link estimates + sale yield).
8. `Snapshot release` — [[code/scripts/publish_snapshot.py]] `--asset-name krisha_rent.db.gz --label "Аренда" --rotate-keep-days 0`.
9. `Track alerts (аренда)` (success only, continue-on-error, 10 min) — `python -m krisha.subscriptions --pull`; `python scripts/send_alerts.py --deal arenda` (tracked rent lots; silent notifications at night). [[code/scripts/send_alerts.py]]
10. `Daily report (аренда)` (always, 5 min) — `python -m krisha.daily_report --scope rent` → admin chat. [[code/src/krisha/daily_report.py]]

## Secrets
`KRISHA_DB_TOKEN` (+ as `GH_TOKEN`), `HF_TOKEN`, `TELEGRAM_BOT_TOKEN`, `STATE_ENCRYPTION_KEY`, `TG_ADMIN_CHAT_ID`.

## Health (2026-10-24..11-02)
10/10 success, 42–85 min.

## Gotchas
- Old step `download && gunzip || echo "no DB yet"` swallowed any error → pass started on empty DB and overwrote history in `db-latest`. Audit 24.10: absence of asset is an error unless explicit `bootstrap=true`.
- `--fail-below 2000` (rent stock ~9k) — catches truncated/blocked search (audit 03.10).
- `--rotate-keep-days 0` for rent snapshots (vs 14 for sale) — rotation deletes only own assets in shared daily snapshot release.
- Restart step added in audit 03.10 (prod stayed on yesterday's rent DB).
- Rent model is retrained in [[ops/workflows/retrain]] job `retrain-rent`, which runs under the SALE concurrency group, so it can overlap this workflow (it only reads the rent DB).

## Related
[[ops/workflows/rescrape]], [[concepts/rent-model]], [[concepts/ci-pipeline]], [[concepts/state-storage]]
