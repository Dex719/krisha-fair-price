---
type: file
path: scripts/release_upload.py
tags: [zone/scripts, layer/ops]
updated: 2026-11-03
---
# scripts/release_upload.py
purpose: crash-safe upload of assets to a GitHub release of the private data repo (audit 24.10). Replaces `gh release upload --clobber`, which deletes before uploading — a mid-way failure left `db-latest`/`model-latest` without the file (rent then treated it as "first run" and overwrote history with an empty DB; prod lived a week without a model).
## Algorithm (per file `<name>`)
1. `<name>` missing but `<name>.uploading` present → previous run died between delete and rename; restore real name
2. delete stale `<name>.uploading` leftovers
3. upload as `<name>.uploading`
4. verify sha256 of uploaded asset (API `digest`, else download) vs local; mismatch → delete temp, old untouched
5. only after ALL files verified: delete old `<name>`, PATCH-rename temp → `<name>`
- file order matters: DB before its `.sha256` (reader checks both). Reader side `krisha.db_release._asset_source` takes the `.uploading` file in the one-API-call window
- `TMP_SUFFIX=".uploading"` :52 must equal `krisha.db_release.UPLOAD_TMP_SUFFIX` (test-enforced); `RETRY_WAITS=(10, 30)`
## Key symbols
- `get_release` :92, `ensure_release` :103, `delete_asset` :125, `rename_asset` :132, `remote_sha256` :140, `_upload_once` :158, `upload` :234, `main` :259
## CLI
- `--repo` (default env `KRISHA_DATA_REPO` or `Dex719/krisha-db`), `--tag` (required), `--create-title`, `--create-notes` (create release if missing — used for `model-latest` and `model-YYYY-MM-DD`), positional `files...`
## Depends on
- stdlib + `gh` CLI in PATH; env `GH_TOKEN`. Paired with [[code/src/krisha/db_release.py]] (reader)
## Used by
- [[ops/workflows/rescrape]] — `--tag db-latest` for `/tmp/sweep_state_prodazha.json`, DB `.gz` + `.sha256` (twice: after sweep and after Alerts)
- [[ops/workflows/rescrape-rent]] — `--tag db-latest` sweep state + rent DB
- [[ops/workflows/retrain]] — `--tag model-latest` and dated `model-$(date -u +%F)` with `/tmp/models.tar.gz` + `.sha256` (sale and rent)
- [[ops/workflows/backfill-gap-cohort]] — `--tag db-latest`
- tests `tests/test_release_upload.py`
## Data / env
- remote: GitHub releases of `Dex719/krisha-db` → [[concepts/state-storage]]
