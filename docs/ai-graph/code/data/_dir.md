---
type: dir
path: data/
tags: [zone/data, layer/storage]
updated: 2026-11-03
---
# data/
purpose: runtime SQLite databases and local state. Only `data/.gitkeep` is tracked; everything else is gitignored (`data/*.db`, `*.db-shm`, `*.db-wal`, `.startup.lock`, `*.csv`, `build_revision.txt`, `rescrape_history_*.json`, `*.json`). Source of truth lives in the private repo `Dex719/krisha-db` → [[concepts/state-storage]].

## Files (local, untracked)
- `krisha.db` — sale DB (`config.DB_PATH`), ~160 MB locally (2026-10-24). Tables created in [[code/src/krisha/db.py]]: `listings`, `price_history`, `sweep_runs`, `sweep_shard_stats`, `sweep_state`, `shard_cursors`, `listing_shards`, `detail_fetch_failures`, `parse_anomalies`, `complexes`, `data_gaps`, `predictions`, `tg_updates`; plus `llm_flags` (schema `CACHE_SCHEMA` in [[code/src/krisha/llm_flags.py]])
- `krisha_rent.db` — rent DB (`config.RENT_DB_PATH`), ~77 MB; price = ₸/month
- `usage_stats.json` — usage counters ([[code/src/krisha/usage.py]]); tests must redirect it (spec test-state-isolation, [[history/kiro-specs]])
- `.startup.lock` — flock between uvicorn workers in [[code/src/krisha/api/app.py]] (no-op on Windows)

## Producers
- [[code/scripts/rescrape.py]] (nightly, [[ops/workflows/rescrape]], [[ops/workflows/rescrape-rent]]), [[code/scripts/crawl.py]], [[code/scripts/crawl_complexes.py]], [[code/scripts/analyze_flags.py]], [[code/scripts/backfill_gap_cohort.py]]
- app at runtime (user predictions, tracking)
## Distribution
- uploaded as `krisha.db.gz` / `krisha_rent.db.gz` + `.sha256` to release `db-latest` by [[code/scripts/release_upload.py]]; dated copies by [[code/scripts/publish_snapshot.py]]; downloaded by [[code/src/krisha/db_release.py]] (prod startup, `KRISHA_DB_AUTO`), verified weekly by [[code/scripts/restore_drill.py]]
## Consumers
- [[code/scripts/train.py]], [[code/scripts/backtest.py]], [[code/scripts/compare_models.py]], [[code/scripts/dedup_stats.py]], [[code/scripts/fetch_osm_zones.py]], [[concepts/web-api]]
## Gotchas
- CI has no real DB; tests use synthetic DBs (`tests/test_backtest.py::_synthetic_db`)
- freshness = end of last `sweep_runs` pass (`db.data_observed_at`), not `MAX(last_seen)`
