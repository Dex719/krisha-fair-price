---
type: file
path: src/krisha/db_release.py
tags: [zone/src, layer/ops, layer/data]
updated: 2026-11-03
---
# src/krisha/db_release.py
purpose: Download/verify/unpack SQLite DBs and model archives from GitHub Releases of the PRIVATE data repo (Dex719/krisha-db): db-latest and model-latest.
concepts: [[concepts/state-storage]], [[concepts/prod-runtime]]
## Key symbols
- `DATA_REPO` — env KRISHA_DATA_REPO, default Dex719/krisha-db `src/krisha/db_release.py:59`
- `RELEASE_TAG` — db-latest: assets krisha.db.gz, krisha_rent.db.gz (+ sweep_state_<deal>.json) `src/krisha/db_release.py:62`
- `MODEL_RELEASE_TAG` — model-latest: models.tar.gz, models_rent.tar.gz `src/krisha/db_release.py:74`
- `MODEL_ARTIFACT_PATHS` — model.cbm, model_quantile.cbm, model_meta.json, spatial_ref.json, complexes.json `src/krisha/db_release.py:79`
- `UPLOAD_TMP_SUFFIX` — '.uploading' — readers accept the temp asset during atomic swap `src/krisha/db_release.py:72`
- `release_asset_urls` — API urls of uploaded assets of a tag (private assets require API + token, not /releases/download/) `src/krisha/db_release.py:121`
- `_verify_checksum` — compare sha256 with `<asset>.sha256` if present; strict mode raises ChecksumUnavailable `src/krisha/db_release.py:201`
- `download` — fetch + checksum + gunzip atomically (tmp → rename) `src/krisha/db_release.py:244`
- `ensure_db` — download sale DB if missing/empty or force; fail-soft `src/krisha/db_release.py:270`
- `ensure_models` — download models if models/model.cbm missing; unpacks inside models/ `src/krisha/db_release.py:344`
- `ensure_rent_db` — same for rent DB `src/krisha/db_release.py:292`
- `ensure_rent_models` — same for models/rent/ `src/krisha/db_release.py:364`
- `main` — CLI `python -m krisha.db_release [--force] [--require] [--models] [--rent] [--strict-checksum]` `src/krisha/db_release.py:378`
## Depends on
- [[code/src/krisha/config.py]]
## Used by
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/scraping/sweep_state_file.py]]
- [[code/scripts/restore_drill.py]]
- [[ops/workflows/ci]]
- [[ops/workflows/rescrape]]
- [[ops/workflows/rescrape-rent]]
- [[ops/workflows/retrain]]
- [[ops/workflows/restore-drill]]
- tests: [[code/tests/_dir|tests/test_db_release.py]], [[code/tests/_dir|tests/test_release_upload.py]], [[code/tests/_dir|tests/test_rent_model.py]], [[code/tests/_dir|tests/test_restore_drill.py]], [[code/tests/_dir|tests/test_sweep_state_file.py]]
## Data / env
- Env: `KRISHA_DB_TOKEN` (token), `KRISHA_DATA_REPO`, `KRISHA_DB_URL`/`KRISHA_MODEL_URL` (override source), `KRISHA_DB_AUTO=0`/`KRISHA_MODEL_AUTO=0` (disable auto-download). Host: api.github.com.
## Gotchas
- Archives are unpacked inside models/ (Space image /app is not writable elsewhere).
- Upload side lives in [[code/scripts/release_upload.py]] (temp name → sha256 → delete old → rename; audit 24.10 replaced `gh release upload --clobber`).
