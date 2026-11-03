---
type: workflow
path: .github/workflows/rescrape.yml
tags: [zone/ops, layer/ci, layer/scraping, deal/prodazha]
updated: 2026-11-03
---
# .github/workflows/rescrape.yml ("Daily rescrape")
purpose: nightly sharded scrape of krisha.kz SALE listings → update SQLite → publish to private release `db-latest` → restart Space → alerts/channel/admin report.

## Trigger
- `schedule: cron "0 1 * * *"` = 01:00 UTC = 06:00 Almaty (UTC+5). Observed actual start 05:50–07:44 UTC (GitHub delays scheduled runs by hours, 2026-10-25..11-03).
- `workflow_dispatch` inputs (all strings): `pages` (default 250, per shard district×rooms), `max_new` (empty → mode preset: drain 4500 / steady 1500), `time_budget_min` (default 300, soft deadline), `delay_min`/`delay_max` (empty → preset drain 1.5–3.0 s / steady 2.0–4.0 s).
- `concurrency: krisha-db-writes`, `cancel-in-progress: false` (shared with [[ops/workflows/retrain]] and [[ops/workflows/backfill-gap-cohort]] deal=prodazha).
- `permissions: contents: read`. `env.KRISHA_DATA_REPO=Dex719/krisha-db`.
- Job `rescrape`, ubuntu-latest, `timeout-minutes: 350`. Python 3.11, `pip install -r requirements.lock` + `pip install -e . --no-deps`.

## Steps (order matters)
1. `Download DB from release` (id download_db) — `python -m krisha.db_release --require` → `data/krisha.db`. [[code/src/krisha/db_release.py]]
2. `Restore sweep state` (continue-on-error, 5 min) — `python -m krisha.scraping.sweep_state_file pull --deal prodazha` merges `sweep_state_prodazha.json` asset (ban_streak, pass_seq) into DB. [[code/src/krisha/scraping/sweep_state_file.py]]
3. `Rescrape` (timeout 315) — `python scripts/rescrape.py --pages ${INPUT_PAGES:-250} [--max-new N] --time-budget-min ${INPUT_TIME_BUDGET_MIN:-300} --fail-empty --fail-below 8000 --summary-json /tmp/rescrape_stats.json`. Env `KRISHA_DELAY_MIN/MAX` from inputs (empty string = preset). [[code/scripts/rescrape.py]] → [[code/src/krisha/scraping/rescrape.py]], [[code/src/krisha/scraping/pass_plan.py]]
4. `Job summary` (always) — stats JSON into `$GITHUB_STEP_SUMMARY`.
5. `Save sweep state` (always && download ok, continue-on-error) — export JSON → `scripts/release_upload.py --tag db-latest /tmp/sweep_state_prodazha.json`. [[code/scripts/release_upload.py]]
6. `Upload DB to release` (id upload_db) — `VACUUM`, `gzip -9` → `krisha.db.gz` + `.sha256` → `release_upload.py --tag db-latest` (atomic: `.uploading` temp name → sha256 verify → rename).
7. `Restart Space` (continue-on-error, 5 min) — `curl -X POST https://huggingface.co/api/spaces/Dex719/krisha-fair-price/restart` with `HF_TOKEN`; skipped if secret empty. Space re-downloads DB on boot.
8. `Snapshot release` (continue-on-error) — [[code/scripts/publish_snapshot.py]] `--asset-name krisha.db.gz --label "Продажа" --rotate-keep-days 14` → release `snapshot-YYYY-MM-DD` (Sunday snapshots kept forever).
9. `Alerts` (id alerts, always, continue-on-error, 15 min) — `python -m krisha.subscriptions --pull` (state from krisha-db), `python -m krisha.db_release --models`, `python scripts/send_alerts.py` (subscriber alerts, channel top-5, /track, morning admin report). [[code/src/krisha/subscriptions.py]], [[code/scripts/send_alerts.py]]
10. `Upload DB again (вердикты алертов)` (always && upload_db ok && alerts ok, 10 min) — re-upload DB because alerts write verdicts to `predictions` table (issue #128).

## Secrets / vars (names only)
`KRISHA_DB_TOKEN` (also passed as `GH_TOKEN` for gh/release_upload), `HF_TOKEN`, `TELEGRAM_BOT_TOKEN`, `STATE_ENCRYPTION_KEY`, `TG_CHANNEL_ID`, `TG_ADMIN_CHAT_ID`.

## Writes to
- Dex719/krisha-db release `db-latest`: `krisha.db.gz`, `krisha.db.gz.sha256`, `sweep_state_prodazha.json`.
- Dex719/krisha-db release `snapshot-<date>`.
- Dex719/krisha-db contents `data/*.json` (state, via send_alerts → save_json_state).
- HF Space restart. Telegram channel + admin chat.

## Health (gh run list, 2026-10-25..11-03)
10/10 success; wall time 187–303 min (approaches 300-min soft budget).

## Failure modes / Gotchas
- `--fail-empty` / `--fail-below 8000` (stock ~44k) turn a SafeLine ban / truncated search into a red run; DB is then NOT uploaded but sweep state is (so ban_streak +1 persists → `pass_plan.BAN_ROLLBACK_STREAK` slows delays). See [[concepts/scraping-krisha]].
- Before #190 §2.3 workflow always forced `--max-new 1000` and delays 2.5–5.0 → drain mode never happened. Empty input now = preset.
- 2026-09-20: job died on timeout in Alerts step, 4h43m of data never uploaded → upload moved before alerts; every secondary step has own `timeout-minutes`. Budget: 300 pass + ~5 install + ~3 upload + 15 alerts + ≤10 re-upload ≈ 333 < 350.
- Security audit 2026-10-06: dispatch inputs only via `env:` (no `${{ }}` in `run:`) — shell injection. Enforced by [[code/tests/test_workflows_hardening.py]] / [[code/tests/_dir|tests/test_rescrape_workflow.py]].
- Audit 24.10: never `gh release upload --clobber` (deletes old first) — use release_upload.py.
- Restart after every rescrape ⇒ in-memory `/api/metrics` and container logs live < 1 day ([[concepts/prod-runtime]]).
- Without `STATE_ENCRYPTION_KEY` in env, alerts derive Fernet key from bot token; token rotation without it breaks state. See [[ops/env-vars]].

## Related
[[concepts/ci-pipeline]], [[concepts/state-storage]], [[ops/workflows/rescrape-rent]]
