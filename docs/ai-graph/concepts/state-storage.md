---
type: concept
tags: [zone/ops, layer/data]
updated: 2026-11-03
---
# State storage (private repo Dex719/krisha-db)
purpose: where all non-code data lives (DBs, model weights, snapshots, bot state) and how it is read/written.

## Decision
2026-10-01 (PR #200, hotfix #201): data, models and bot state moved to PRIVATE GitHub repo `Dex719/krisha-db`; code stays in PUBLIC `Dex719/krisha-fair-price` (free Actions minutes). No encryption of data/models — access control only (bot state files ARE Fernet-encrypted, see below). Old public data releases deleted, history rewritten with git filter-repo. Access: fine-grained PAT `KRISHA_DB_TOKEN` (Contents RW on krisha-db only), stored in Actions secrets, Dependabot secrets and HF Space.

## Releases in krisha-db
| tag | assets | writer | reader |
|---|---|---|---|
| `db-latest` (mutable) | `krisha.db.gz`, `.sha256`; `krisha_rent.db.gz`, `.sha256`; `sweep_state_prodazha.json`, `sweep_state_arenda.json` | [[ops/workflows/rescrape]], [[ops/workflows/rescrape-rent]], [[ops/workflows/backfill-gap-cohort]] | Space boot, retrain, restore-drill |
| `model-latest` (mutable) | `models.tar.gz` (+.sha256: model.cbm, model_quantile.cbm, model_meta.json, spatial_ref.json, complexes.json), `models_rent.tar.gz` (+.sha256: rent model.cbm, model_quantile.cbm, model_meta.json, spatial_ref.json) | [[ops/workflows/retrain]] | Space boot, CI (needs_model tests), rescrape Alerts, restore-drill |
| `model-YYYY-MM-DD` | same archives, dated | retrain | manual rollback |
| `snapshot-YYYY-MM-DD` | daily DB copies + sha256, pass summary in release body | [[code/scripts/publish_snapshot.py]] | manual restore; sale keeps 14 days except Sundays (forever); rent `--rotate-keep-days 0` |
Constants: [[code/src/krisha/db_release.py]] (`RELEASE_TAG`, `ASSET_NAME`, `RENT_ASSET_NAME`, `MODEL_RELEASE_TAG`, `MODEL_ASSET_NAME`, `RENT_MODEL_ASSET_NAME`, `MODEL_ARTIFACT_PATHS`, `RENT_MODEL_ARTIFACT_PATHS`, `UPLOAD_TMP_SUFFIX=".uploading"`).

## Write protocol (audit 24.10)
[[code/scripts/release_upload.py]]: upload as `<name>.uploading` → verify sha256 (API digest or re-download) → after ALL files verified delete old + rename temp. Leftover `.uploading` with missing original is promoted. Readers (`db_release._asset_source`) accept the `.uploading` name during the one-call window. DB before its `.sha256`. Never `gh release upload --clobber`.

## Read protocol
`python -m krisha.db_release [--rent] [--models] [--require] [--force] [--strict-checksum]`; runtime `ensure_db/ensure_models/ensure_rent_db/ensure_rent_models` (fail-soft, skip if present; `KRISHA_DB_AUTO=0` / `KRISHA_MODEL_AUTO=0` disable; `KRISHA_DB_URL`/`KRISHA_MODEL_URL` override source).

## Bot state (contents API, `data/*.json` in krisha-db)
Files (`STATE_FILES` in [[code/src/krisha/subscriptions.py]]): `subscriptions.json`, `tracked.json`, `usage_stats.json`, `prediction_log.json`, `channel_posted.json`, `alerted_ids.json`, `alerted_chats.json`. Written by `save_json_state` (Space runtime on /alerts_on, /track, usage flush; Actions alert steps); pulled by `pull_state()` at Space boot (`KRISHA_STATE_PULL=0` disables) and `python -m krisha.subscriptions --pull` in Actions. Repo override `KRISHA_STATE_REPO`. subscriptions/tracked encrypted with Fernet key = sha256("kfp-state:" + (`STATE_ENCRYPTION_KEY` or `TELEGRAM_BOT_TOKEN`)) — rotation order in [[ops/env-vars]]. `STATE_LOCK` RLock serializes writers inside a process.

## What stays in public git
`models/model_meta.json`, `models/metrics_history.jsonl`, `models/stats.json`, `models/complexes.json`, `models/osm_pois.json`, `models/osm_zones.json`, `models/rent/model_meta.json`, `models/rent/metrics_history.jsonl`, `reports/`. Weights (`*.cbm`), `spatial_ref.json`, gate samples, `data/*.db`, `data/*.json`, `data/build_revision.txt` are gitignored.

## Inside the SQLite DBs (pointer)
Listings, price history, `sweep_runs`, `sweep_state` (`ban_streak:<deal>`, `pass_seq:<deal>`), `predictions` verdicts, `tg_updates` dedup. Schema owned by [[code/src/krisha/db.py]]; sweep state mirror file by [[code/src/krisha/scraping/sweep_state_file.py]].

## Gotchas
- `db-latest` is the ONLY live copy of accumulated history; snapshots are the backup; [[ops/workflows/restore-drill]] proves restorability weekly.
- In Space image `/app` is root-owned: temp files during download must be inside `models/` or `data/`.
- A failed pass does not upload DB but does upload sweep_state JSON (so ban streak survives).
- Owner decision: removal of releases / history rewriting / branch deletion only with explicit owner confirmation.
