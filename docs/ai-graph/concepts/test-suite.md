---
type: concept
tags: [zone/tests]
updated: 2026-11-03
---
# test-suite
purpose: how the project is tested, which categories exist, and which regressions are pinned (with issue/PR numbers).

## Run
- `pytest -q` (default excludes `e2e`, `live`), `pytest -m e2e -q`, `pytest -m live` (manual). `needs_model` skips without models/model.cbm. Details → [[code/tests/_dir]], [[code/tests/conftest.py]].
- CI: [[ops/workflows/ci]] (units + e2e jobs); [[ops/workflows/retrain]] gates on artifact tests. See [[concepts/ci-pipeline]].

## Categories
- Unit/logic with synthetic data in tmp_path: db, features, train, predict, interval, scraping parsers, rescrape, bot, alerts, tracking.
- Page/markup contracts (string tests over static HTML + TestClient): test_home_*, test_page_*, test_site_shell, test_market_redesign, test_seo_live_pages → [[concepts/frontend]].
- API hardening: test_api_security, test_api_load_hardening, test_predict_gate, test_api_audit_2026_10_24, test_multiworker_safety, test_static_precompress → [[concepts/web-api]].
- Artifact gates on real files: test_model_artifacts, test_rent_model_artifacts → [[concepts/sale-price-model]], [[concepts/rent-model]].
- Scripts (loaded via importlib/sys.path): smoke_prod, model_gate, backtest, publish_snapshot, release_upload, send_alerts, health_check, restore_drill, rescrape CLI.
- Workflow/infra static checks: test_workflows_hardening, test_rescrape_workflow, test_retrain_workflow, test_state_key_workflows, test_security_txt, test_runtime_lock.
- Source-scan guards: test_no_direct_sqlite_connect.
- Browser e2e + live smoke → [[code/tests/e2e/_dir]].

## Regression guards (why)
- #74 weights/db not in git → `needs_model` skip, publish_snapshot tests.
- #81/#82 about page, rebranding texts, README.
- #97 rescrape CLI exit codes, parse-rate history; #99 detail parser; #100 window.data JSON bracket parsing; #101 403 ban vs 429 vs 468 SafeLine challenge ([[concepts/scraping-krisha]]).
- #103 data contracts/quarantine on upsert; #106 model gate fail-closed; #109 forecast; #111/#150 state encryption; #113 API over real HTTP/security headers.
- #115 no raw sqlite3.connect (database is locked on hot path) → [[code/tests/test_no_direct_sqlite_connect.py]].
- #116 usage stats; #119 lean prod runtime lock; #128 durable prediction log; #129 spatial; #130/#131 backtest, targets.
- #152 drain mode/pass plan/dedup stats; #154 daily report verdict; #156 recovery-gap backfill cohort; #157 feature flags + scam warning; #158 validity; #166 shard plan; #171 sweep run precision; #190 probes/SEO/request id; #194 factor hints KD cache.
- PR #237 STATE_ENCRYPTION_KEY rotation in Actions; security audit 2026-10-06 (#235–#237) → workflows hardening, security.txt.
- Audit 24.10 (PRs #240–#244): test_api_audit_2026_10_24, test_alerts_delivery, test_bot_audit_fixes, test_bot_state_merge, test_backtest_alignment, test_model_gate_audit, test_train_audit, test_release_upload, test_sweep_state_file, test_compare_models_refit.
- UX audit 2026-10-15: test_ux_audit_fixes; SEO audit 05.10: test_seo_live_pages.
- Specs in `.kiro/specs/*` referenced: rescrape-post-steps, predict-edge-listings, krisha-session-warmup, test-state-isolation.

## Gotchas
- Every new module-level cache/limiter must be reset in conftest `_clear_api_caches`.
- Tests must never write into repo `data/` — redirect paths to tmp_path.
- CI installs train lock versions; prod uses runtime lock — a "locally red" test can be a prod bug.
