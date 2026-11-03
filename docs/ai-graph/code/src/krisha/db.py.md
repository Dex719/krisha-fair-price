---
type: file
path: src/krisha/db.py
tags: [zone/src, layer/data]
updated: 2026-11-03
---
# src/krisha/db.py
purpose: SQLite storage layer for both sale and rent DBs: schema + migrations, connection helper, listing upserts with data-contract quarantine, sightings, price history, predictions log, sweep bookkeeping, Telegram update dedup.
concepts: [[concepts/scraping-krisha]], [[concepts/state-storage]]
## Key symbols
- `SCHEMA` — tables listings, tg_updates, price_history, predictions, complexes, parse_anomalies, sweep_runs, data_gaps, listing_shards, shard_cursors, sweep_shard_stats, sweep_state, detail_fetch_failures `src/krisha/db.py:33`
- `LISTING_COLUMNS` — persisted listing fields (id, url, title, price, rooms, area, floor, …, raw_params) `src/krisha/db.py:288`
- `UPSERT_SQL` — scrape upsert: price/title/raw_params always overwritten, others COALESCE; does NOT touch last_seen/is_active/delisted_at on conflict (audit 24.10) `src/krisha/db.py:312`
- `UPSERT_SQL_USER` — source='user' upsert: never overwrites price/title/raw_params on conflict (issue #117) `src/krisha/db.py:349`
- `get_conn` — contextmanager: WAL, busy_timeout=5000, synchronous=NORMAL, mmap 256MB, cache 8MB; commits on exit `src/krisha/db.py:379`
- `use_conn` — reuse a caller-provided connection (issue #110: one conn per /api/predict) `src/krisha/db.py:410`
- `_migrate` — adds missing columns/indexes to old DBs (CREATE IF NOT EXISTS doesn't add columns) `src/krisha/db.py:473`
- `init_db` — create schema + migrate `src/krisha/db.py:570`
- `listing_fingerprint` — apartment fingerprint for cross-id duplicate detection `src/krisha/db.py:636`
- `find_duplicate_id` — other id with same fingerprint (freshest first) `src/krisha/db.py:657`
- `price_bounds_for` — deal → (min,max) price contract `src/krisha/db.py:682`
- `_validate_and_quarantine` — data-contract check; bad fields logged to parse_anomalies (issue #103) `src/krisha/db.py:723`
- `upsert_listing` — main write path; computes fingerprint and coords_approx; user source writes no price_history point `src/krisha/db.py:809`
- `record_sighting` — cheap 'seen in search' row without detail page (issue #127) `src/krisha/db.py:876`
- `_record_price_if_changed` — append price_history point when price changed `src/krisha/db.py:992`
- `log_prediction` — row into predictions (issue #128) + durable prediction_log `src/krisha/db.py:919`
- `log_predictions` — batch insert for alerts `src/krisha/db.py:961`
- `record_sweep_run` — pass counters → sweep_runs (issue #154) `src/krisha/db.py:1076`
- `data_observed_at` — single source for 'when collector last walked the market' (health/stats freshness) `src/krisha/db.py:1155`
- `shard_backlog_window_fresh_first` — detail window per shard: fresh (48h) above cursor first, then sequential from cursor `src/krisha/db.py:1343`
- `zero_quota_streak` — consecutive passes a shard had zero quota with backlog `src/krisha/db.py:1419`
- `consecutive_bans` — ban streak from sweep_state `src/krisha/db.py:1532`
- `advance_sweep_pass_seq` — monotonic pass counter (issue #168) `src/krisha/db.py:1507`
- `remember_update_id` — cross-worker Telegram update_id dedup in tg_updates (keeps last 5000); fail-soft True `src/krisha/db.py:1575`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/prediction_log.py]]
## Used by
- [[code/src/krisha/alerts.py]]
- [[code/src/krisha/analogs.py]]
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/daily_report.py]]
- [[code/src/krisha/factor_hints.py]]
- [[code/src/krisha/llm_flags.py]]
- [[code/src/krisha/market.py]]
- [[code/src/krisha/monitoring.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/rental_yield.py]]
- [[code/src/krisha/scraping/crawler.py]]
- [[code/src/krisha/scraping/rescrape.py]]
- [[code/src/krisha/scraping/sweep_state_file.py]]
- [[code/src/krisha/stats.py]]
- [[code/src/krisha/tracking.py]]
- [[code/src/krisha/train.py]]
- [[code/src/krisha/vision.py]]
- [[code/scripts/analyze_flags.py]]
- [[code/scripts/backfill_gap_cohort.py]]
- [[code/scripts/crawl_complexes.py]]
- [[code/scripts/restore_drill.py]]
- tests: [[code/tests/_dir|tests/test_alerts.py]], [[code/tests/_dir|tests/test_alerts_batch.py]], [[code/tests/_dir|tests/test_almaty_only.py]], [[code/tests/_dir|tests/test_analogs.py]], [[code/tests/_dir|tests/test_api_load_hardening.py]], [[code/tests/_dir|tests/test_backfill_gap_cohort.py]], [[code/tests/_dir|tests/test_backtest.py]], [[code/tests/_dir|tests/test_backtest_alignment.py]], [[code/tests/_dir|tests/test_bot.py]], [[code/tests/_dir|tests/test_bot_audit_fixes.py]], [[code/tests/_dir|tests/test_bot_state_merge.py]], [[code/tests/_dir|tests/test_daily_report_verdict.py]], [[code/tests/_dir|tests/test_data_contracts.py]], [[code/tests/_dir|tests/test_db.py]], [[code/tests/_dir|tests/test_dedup_stats.py]], [[code/tests/_dir|tests/test_drain_mode.py]], [[code/tests/_dir|tests/test_factor_hints.py]], [[code/tests/_dir|tests/test_health_freshness.py]], [[code/tests/_dir|tests/test_home_redesign.py]], [[code/tests/_dir|tests/test_market.py]], [[code/tests/_dir|tests/test_monitoring.py]], [[code/tests/test_multiworker_safety.py]], [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_predict_edge_listings.py]], [[code/tests/_dir|tests/test_prediction_log.py]], [[code/tests/_dir|tests/test_probes_and_seo.py]], [[code/tests/_dir|tests/test_rent_serving.py]], [[code/tests/test_rescrape.py]], [[code/tests/_dir|tests/test_rescrape_safeline_streak.py]], [[code/tests/_dir|tests/test_restore_drill.py]], [[code/tests/_dir|tests/test_session_warmup.py]], [[code/tests/test_shard_plan.py]], [[code/tests/_dir|tests/test_stats.py]], [[code/tests/_dir|tests/test_sweep_run_precision.py]], [[code/tests/_dir|tests/test_sweep_state_file.py]], [[code/tests/_dir|tests/test_tracking.py]], [[code/tests/_dir|tests/test_train_smoke.py]], [[code/tests/_dir|tests/test_ux_audit_fixes.py]]
## Data / env
- Files: `data/krisha.db`, `data/krisha_rent.db` (same schema). No env vars.
## Gotchas
- Detail pages open for archived lots too → detail refetch/user check must never revive delisted lots; only sighting and sweep delist set last_seen/is_active.
- `sqlite3.connect` outside db.py is forbidden by [[code/tests/test_no_direct_sqlite_connect.py]] (allowlist: stats.py, train.py, complexes.py).
- Upserts retry after `executescript(SCHEMA)+_migrate` on OperationalError — downloaded old DBs self-migrate.
- Index on (lat, lon) is created only if coordinates exist (old DBs).
- sweep_runs.started_at is millisecond precision (issue #171) — history used to overwrite itself.
