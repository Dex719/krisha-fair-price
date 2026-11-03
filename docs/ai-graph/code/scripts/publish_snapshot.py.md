---
type: file
path: scripts/publish_snapshot.py
tags: [zone/scripts, layer/ops]
updated: 2026-11-03
---
# scripts/publish_snapshot.py
purpose: issue #74 part 1 — after each successful rescrape publish/extend a dated release `snapshot-YYYY-MM-DD` in the private data repo with compressed DB(s), sha256 and a human-readable pass summary; rotate old snapshots. Complements mutable `db-latest` (untouched).
## Key symbols
- `REPO = env KRISHA_DATA_REPO or "Dex719/krisha-db"` :39, `TAG_PREFIX="snapshot"`, `PUBLISH_RETRY_WAITS=(10, 30)`
- `human_summary(label, stats)` :57, `publish(...)` :98, `_ensure_published` :154 (no drafts left), `_edit_notes` :167, `rotate(keep_days, asset_name)` :183, `main` :236
## Rotation
- snapshots older than `--rotate-keep-days` deleted EXCEPT Sundays (`weekday()==6`, kept forever)
- deletes only its own assets (`--asset-name` + `.sha256`) — the same day's release also holds the rent DB; release deleted when empty (`--cleanup-tag`)
- fail-soft: rotation errors never fail the rescrape workflow
## CLI
- `--db-gz` (req), `--sha256` (req), `--asset-name` (req, e.g. `krisha.db.gz`), `--stats-json` (rescrape `--summary-json`), `--label` (default «Рескрейп»), `--tag` (default `snapshot-<today UTC>`), `--rotate-keep-days 14` (0 = no rotation)
## Depends on
- stdlib + `gh` CLI; env `GH_TOKEN` (workflow: `secrets.KRISHA_DB_TOKEN`)
## Used by
- [[ops/workflows/rescrape]] — `--db-gz /tmp/krisha.db.gz --sha256 /tmp/krisha.db.gz.sha256 --asset-name krisha.db.gz --stats-json /tmp/rescrape_stats.json --label "Продажа" --rotate-keep-days 14` (step "Snapshot release", continue-on-error)
- [[ops/workflows/rescrape-rent]] — same for rent DB
- tests `tests/test_publish_snapshot.py`
## Data / env
- [[concepts/state-storage]]
## Gotchas
- `_edit_notes` writes temp notes with `encoding="utf-8"` — on Windows cp1252 Cyrillic crashed (fixed under spec windows-local-dev, [[history/kiro-specs]])
- retries transient failures and leaves no draft releases (spec rescrape-post-steps)
