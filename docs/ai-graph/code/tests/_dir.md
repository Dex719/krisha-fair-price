---
type: dir
path: tests/
tags: [zone/tests]
updated: 2026-11-03
---
# tests/
purpose: pytest suite (~110 unit/contract test files + 8 browser e2e + live smoke); hermetic, synthetic data in tmp_path, no network.

## Layout
- `tests/*.py` — unit, contract and regression tests (flat; most docstrings in Russian, many cite issue/PR numbers).
- `tests/e2e/` — Playwright + uvicorn browser/HTTP tests → [[code/tests/e2e/_dir]].
- `tests/fixtures/` — HTML samples for parsers → [[code/tests/fixtures/_dir]].
- `tests/conftest.py` — global env + autouse isolation → [[code/tests/conftest.py]].

## How tests run
- Config `pyproject.toml [tool.pytest.ini_options]`: `testpaths=["tests"]`, `pythonpath=["."]` (needed because [[code/tests/test_runtime_lock.py]] imports non-package `scripts/`), `addopts = "-m 'not e2e and not live'"`.
- Markers: `e2e` (hermetic browser, uvicorn + chromium, network mocked), `live` (real DB + one request to krisha.kz, manual only), `needs_model` (real `models/model.cbm`; skipped if absent — weights live in private release, issue #74).
- Commands: `pytest -q` / `make test` (units); `pytest -m e2e -q` / Makefile e2e target; `pytest -m live` manually.
- CI: [[ops/workflows/ci]] runs `pytest -q` and a separate job `pytest -m e2e -q` (installs `pytest-playwright`). [[ops/workflows/retrain]] runs `tests/test_model_artifacts.py` and `tests/test_rent_model_artifacts.py` as gates after training.
- Some tests load scripts via `importlib.util.spec_from_file_location` or `sys.path.insert(ROOT/"scripts")` (scripts/ is not a package).
- Workflow tests parse `.github/workflows/*.yml`; behavioural ones execute bash snippets and skip without bash.

## Fixtures / conftest
- Env set in [[code/tests/conftest.py]]: `KRISHA_DB_AUTO=0`, `KRISHA_MODEL_AUTO=0`, `KRISHA_STATE_PULL=0`, `USAGE_FLUSH_SYNC=1`, `KRISHA_SESSION_WARMUP=0`, `KRISHA_LIVE_PAGES=0`.
- Autouse `_isolate_repo_state` (usage stats → tmp_path, GitHub state push stubbed) and `_clear_api_caches` (API TTL caches, predict_gate, metrics, rate limiter, bot limiter, Gemini parse cache — before and after each test).
- Per-file autouse fixtures commonly redirect `DATA_DIR`/DB paths to tmp_path (e.g. [[code/tests/test_rescrape.py]]).

## Test file → covered modules
| Test | Covers | Checks |
|---|---|---|
| test_about_page.py | [[code/src/krisha/api/app.py]], [[code/static/_dir]] | /about page acceptance (issue #81), CSP |
| test_alerts.py | [[code/src/krisha/alerts.py]], [[code/src/krisha/subscriptions.py]], [[code/src/krisha/stats.py]] | filter parse/match, alert format, trend |
| test_alerts_batch.py | [[code/src/krisha/alerts.py]], [[code/src/krisha/predict.py]], [[code/src/krisha/factor_hints.py]] | batch alert evaluation after rescrape (needs_model) |
| test_alerts_delivery.py | [[code/src/krisha/alerts.py]] | delivery pacing, Telegram 429, per-chat log, dead chats (audit 24.10) |
| test_almaty_only.py | [[code/src/krisha/predict.py]], [[code/src/krisha/bot.py]], [[code/src/krisha/api/app.py]] | non-Almaty listing rejected with reason |
| test_analogs.py | [[code/src/krisha/analogs.py]] | kNN analog selection/ranking |
| test_api_audit_2026_10_24.py | [[code/src/krisha/api/app.py]], [[code/src/krisha/api/cache.py]], [[code/src/krisha/predict_gate.py]] | audit 24.10 API regressions → [[code/tests/test_api_audit_2026_10_24.py]] |
| test_api_load_hardening.py | [[code/src/krisha/api/app.py]], [[code/src/krisha/api/cache.py]], [[code/src/krisha/predict_gate.py]] | caches, single-flight, limits under load |
| test_api_rent_stats.py | [[code/src/krisha/api/app.py]] | /api/stats/rent |
| test_api_schema.py | [[code/src/krisha/api/schemas.py]] | PredictResponse does not drop predict_from_listing fields |
| test_api_security.py | [[code/src/krisha/api/app.py]] | CSP/nosniff/Referrer-Policy headers, MAX_BODY_BYTES |
| test_backfill_gap_cohort.py | [[code/scripts/backfill_gap_cohort.py]], [[code/src/krisha/db.py]] | retro-marking orphaned recovery cohort (issue #156) |
| test_backtest.py | [[code/scripts/backtest.py]] | walk-forward honest eval (issue #130) |
| test_backtest_alignment.py | [[code/scripts/backtest.py]], [[code/src/krisha/train.py]] | backtest slices data same as prod load_dataset |
| test_bot.py | [[code/src/krisha/bot.py]], [[code/src/krisha/predict.py]], [[code/src/krisha/tracking.py]] | Telegram formatting & update handling |
| test_bot_audit_fixes.py | [[code/src/krisha/bot.py]], [[code/src/krisha/llm_flags.py]] | webhook, buttons, tg-proxy secret, llm_flags batches |
| test_bot_buttons.py | [[code/src/krisha/bot.py]], [[code/src/krisha/alerts.py]], [[code/src/krisha/tracking.py]] | inline buttons and their actions |
| test_bot_limits.py | [[code/src/krisha/bot.py]], [[code/src/krisha/predict_gate.py]] | per-chat sliding-window limits, 3 buckets |
| test_bot_state_merge.py | [[code/src/krisha/subscriptions.py]], [[code/src/krisha/tracking.py]] | tracking state merge across workers/Actions |
| test_channel.py | [[code/src/krisha/channel.py]] | public channel digest |
| test_city_center.py | [[code/src/krisha/features.py]], [[code/src/krisha/config.py]], [[code/src/krisha/train.py]] | fixed city center vs LEGACY center for old models |
| test_compare_models_refit.py | [[code/scripts/compare_models.py]] | compare after refit-on-all-data |
| test_complexes.py | [[code/src/krisha/complexes.py]], [[code/src/krisha/scraping/complex_parser.py]], [[code/src/krisha/features.py]] | ЖК name normalization, parser, features |
| test_daily_report.py | [[code/src/krisha/daily_report.py]], [[code/scripts/health_check.py]] | admin daily report, health-check |
| test_daily_report_verdict.py | [[code/src/krisha/daily_report.py]] | ОК/ПРОБЛЕМА verdict (issue #154) |
| test_data_contracts.py | [[code/src/krisha/db.py]], [[code/src/krisha/config.py]] | upsert_listing data contract + quarantine (issue #103) |
| test_db.py | [[code/src/krisha/db.py]] | schema, upserts, delist logic |
| test_db_release.py | [[code/src/krisha/db_release.py]] | DB/model download from private releases |
| test_dedup_stats.py | [[code/scripts/dedup_stats.py]] | dedup stats smoke (issue #152) |
| test_detail_parser.py | [[code/src/krisha/scraping/detail_parser.py]], [[code/src/krisha/scraping/listing_parser.py]] | parse detail page/listing cards (uses fixtures/detail_sample.html) |
| test_drain_mode.py | [[code/src/krisha/scraping/rescrape.py]], [[code/src/krisha/scraping/pass_plan.py]], [[code/src/krisha/daily_report.py]] | drain vs steady mode, ban rollback (issue #152) |
| test_factor_hints.py | [[code/src/krisha/factor_hints.py]], [[code/src/krisha/spatial.py]] | floor hints, KD-tree cache (PR #194) |
| test_feature_flags.py | [[code/src/krisha/config.py]], [[code/src/krisha/predict.py]], [[code/src/krisha/vision.py]] | feature flags for unverified features (issue #157) |
| test_features.py | [[code/src/krisha/features.py]] | feature engineering |
| test_forecast.py | [[code/src/krisha/forecast.py]] | district price trend forecast |
| test_health_check_script.py | [[code/scripts/health_check.py]] | stale data = degraded, not up |
| test_health_freshness.py | [[code/src/krisha/api/app.py]], [[code/src/krisha/db.py]] | /api/health freshness |
| test_home_polish.py | [[code/static/_dir]] | small home-page markup contracts |
| test_home_redesign.py | [[code/src/krisha/api/app.py]], [[code/src/krisha/predict.py]], [[code/static/_dir]] | home page contract after baǵam redesign |
| test_interval.py | [[code/src/krisha/interval.py]], [[code/src/krisha/predict.py]] | interval verdict + conformal coverage |
| test_issue82_texts.py | [[code/src/krisha/bot.py]] | rebranding texts (issue #82) |
| test_json_window.py | [[code/src/krisha/scraping/json_window.py]] | bracket-correct window.data parse (issue #100) |
| test_llm_flags.py | [[code/src/krisha/llm_flags.py]] | Gemini flags cache, parse, badges |
| test_market.py | [[code/src/krisha/market.py]], [[code/src/krisha/db.py]], [[code/src/krisha/scraping/listing_parser.py]] | price history, days on market |
| test_market_redesign.py | [[code/src/krisha/api/app.py]], [[code/static/_dir]] | /stats page contract (sale+rent) |
| test_model_artifacts.py | [[code/src/krisha/predict.py]], [[code/src/krisha/features.py]], [[code/src/krisha/interval.py]], [[code/src/krisha/spatial.py]] | real models/ artifacts consistency → [[code/tests/test_model_artifacts.py]] |
| test_model_gate.py | [[code/scripts/model_gate.py]] | coverage gate, fail-closed, bootstrap (issue #106) |
| test_model_gate_audit.py | [[code/scripts/model_gate.py]] | cluster bootstrap, narrow tolerance (audit 24.10) |
| test_monitoring.py | [[code/src/krisha/monitoring.py]] | metrics history, retrain report |
| test_multiworker_safety.py | [[code/src/krisha/usage.py]], [[code/src/krisha/db.py]], [[code/src/krisha/api/app.py]] | multi-worker safety → [[code/tests/test_multiworker_safety.py]] |
| test_no_direct_sqlite_connect.py | [[code/src/krisha/db.py]] (all src/krisha) | no new raw sqlite3.connect → [[code/tests/test_no_direct_sqlite_connect.py]] |
| test_page_bot.py | [[code/src/krisha/api/app.py]], [[code/src/krisha/bot.py]], [[code/src/krisha/subscriptions.py]] | /bot page describes only real bot features |
| test_page_privacy.py | [[code/src/krisha/api/app.py]], [[code/src/krisha/api/site_analytics.py]], [[code/src/krisha/prediction_log.py]] | /privacy matches actual data handling |
| test_page_rent.py | [[code/src/krisha/api/app.py]] | /rent → /stats?mode=rent redirect |
| test_page_terms.py | [[code/src/krisha/api/app.py]] | /terms page |
| test_pass_plan.py | [[code/src/krisha/scraping/pass_plan.py]], [[code/src/krisha/scraping/shard_plan.py]] | mode by backlog, timings (issue #152) |
| test_predict.py | [[code/src/krisha/predict.py]] | CQR, money impact, explain_price |
| test_predict_edge_listings.py | [[code/src/krisha/predict.py]], [[code/src/krisha/api/app.py]] | listing without price / delisted (needs_model) |
| test_predict_gate.py | [[code/src/krisha/predict_gate.py]], [[code/src/krisha/api/metrics.py]] | predict gate: cache, negative cache, slots |
| test_predict_price_history_api.py | [[code/src/krisha/api/app.py]] | price history in predict API |
| test_prediction_log.py | [[code/src/krisha/prediction_log.py]] | durable prediction log (#128) |
| test_probes_and_seo.py | [[code/src/krisha/api/app.py]] | probes, request id, SEO (issue #190) |
| test_publish_snapshot.py | [[code/scripts/publish_snapshot.py]] | snapshot publishing logic (issue #74) |
| test_readme.py | [[code/scripts/sync_readme_metrics.py]] | README acceptance (issue #82) |
| test_release_upload.py | [[code/scripts/release_upload.py]], [[code/src/krisha/db_release.py]] | upload without "file missing" window |
| test_rent_model.py | [[code/src/krisha/rent.py]], [[code/src/krisha/model_spec.py]], [[code/src/krisha/train.py]] | rent spec, room-share filter, training |
| test_rent_model_artifacts.py | [[code/src/krisha/model_spec.py]] | real models/rent/ consistency (retrain gate) |
| test_rent_serving.py | [[code/src/krisha/predict.py]], [[code/src/krisha/rent.py]], [[code/src/krisha/rental_yield.py]] | deal detection, rent model, yield |
| test_report.py | [[code/src/krisha/report.py]] | monthly market report |
| test_rescrape.py | [[code/src/krisha/scraping/rescrape.py]], [[code/src/krisha/db.py]] | sharded rescrape → [[code/tests/test_rescrape.py]] |
| test_rescrape_cli.py | [[code/scripts/rescrape.py]] | --fail-empty/--fail-below exit codes (issue #97) |
| test_rescrape_workflow.py | [[ops/workflows/rescrape]], [[code/scripts/release_upload.py]] | nightly pass step order |
| test_restore_drill.py | [[code/scripts/restore_drill.py]], [[code/src/krisha/db_release.py]] | restore-from-release drill |
| test_retrain_workflow.py | [[ops/workflows/retrain]], [[code/scripts/train.py]] | train commands in retrain.yml parse |
| test_runtime_lock.py | [[code/scripts/gen_runtime_lock.py]] | prod lock excludes plotting deps (issue #119) |
| test_runtime_optimizations.py | [[code/src/krisha/predict.py]], [[code/src/krisha/geo.py]], [[code/src/krisha/spatial.py]] | HF runtime optimizations |
| test_scam.py | [[code/src/krisha/scam.py]] | "suspiciously cheap" warning (issue #157) |
| test_scraping_client.py | [[code/src/krisha/scraping/client.py]] | 403 ban vs 429 vs 468 challenge (issue #101) |
| test_security_txt.py | [[code/src/krisha/api/app.py]] | security.txt etc. (security audit 2026-10-06) |
| test_send_alerts.py | [[code/scripts/send_alerts.py]] | failed window is loud, tracking still sent |
| test_send_alerts_rent.py | [[code/scripts/send_alerts.py]] | --deal arenda only rent tracking |
| test_seo_live_pages.py | [[code/src/krisha/api/live_pages.py]], [[code/src/krisha/api/site_analytics.py]] | live numbers in markup, JSON-LD |
| test_session_warmup.py | [[code/src/krisha/scraping/client.py]], [[code/src/krisha/predict.py]] | krisha session warmup at startup |
| test_shard_plan.py | [[code/src/krisha/scraping/shard_plan.py]], [[code/src/krisha/scraping/rescrape.py]], [[code/src/krisha/db.py]] | shard quotas/cursors → [[code/tests/test_shard_plan.py]] |
| test_site_shell.py | [[code/static/_dir]] | head, header, mobile menu, footer, site.js |
| test_smoke_prod.py | [[code/scripts/smoke_prod.py]] | prod smoke script → [[code/tests/test_smoke_prod.py]] |
| test_spatial.py | [[code/src/krisha/spatial.py]], [[code/src/krisha/train.py]] | hexes, KNN spatial lag, dedup/split |
| test_state_encryption.py | [[code/src/krisha/subscriptions.py]], [[code/src/krisha/tracking.py]] | state file encryption (#111, #150) |
| test_state_key_workflows.py | [[code/src/krisha/subscriptions.py]], [[ops/workflows/_dir]] | STATE_ENCRYPTION_KEY in Actions (PR #237) |
| test_state_repo.py | [[code/src/krisha/subscriptions.py]], [[code/src/krisha/usage.py]] | bot state lives in private data repo |
| test_static_precompress.py | [[code/src/krisha/api/static_cache.py]] | precompressed static, ETag, 304, HEAD |
| test_stats.py | [[code/src/krisha/stats.py]] | market stats, heatmap |
| test_sweep_run_precision.py | [[code/src/krisha/db.py]] | two passes same second not merged (issue #171) |
| test_sweep_state_file.py | [[code/src/krisha/scraping/sweep_state_file.py]] | ban streak/pass no. survive crashed pass |
| test_targets.py | [[code/src/krisha/targets.py]] | target modes, city index, freshness weight (issue #131) |
| test_text_parse.py | [[code/src/krisha/text_parse.py]] | estimate from pasted text (Gemini) |
| test_text_parse_cache.py | [[code/src/krisha/text_parse.py]] | parse cache saves key quota |
| test_tracking.py | [[code/src/krisha/tracking.py]], [[code/src/krisha/bot.py]] | /track storage and change alerts |
| test_train_audit.py | [[code/src/krisha/train.py]] | train fixes after audit 24.10 |
| test_train_refit.py | [[code/src/krisha/train.py]] | refit on all data; gate compares successor |
| test_train_smoke.py | [[code/src/krisha/train.py]] | synthetic training smoke, time split |
| test_usage.py | [[code/src/krisha/usage.py]] | usage events, weekly report (issue #116) |
| test_ux_audit_fixes.py | [[code/src/krisha/_dir|src/krisha/numfmt.py]], [[code/src/krisha/rent.py]], [[code/src/krisha/scraping/detail_parser.py]] | UX audit 2026-10-15 backend fixes |
| test_validity.py | [[code/src/krisha/validity.py]] | measurement validity (issue #158) |
| test_vision.py | [[code/src/krisha/vision.py]] | renovation estimate from photos |
| test_workflows_hardening.py | [[ops/workflows/_dir]] | Actions hardening → [[code/tests/test_workflows_hardening.py]] |
| test_zones.py | [[code/src/krisha/zones.py]] | point-in-polygon, district recovery |

## Used by
- [[concepts/test-suite]], [[concepts/ci-pipeline]]
