---
type: dir
path: scripts/
tags: [zone/scripts]
updated: 2026-11-03
---
# scripts/
purpose: CLI entry points (22 tracked .py). Thin wrappers over `src/krisha` for ML/scraping, plus stdlib/httpx-only ops tools run by GitHub workflows. Most `sys.path.insert(src)` or rely on `pip install -e .`; tests import some as `scripts.<name>` (e.g. `scripts.gen_runtime_lock`).

## By role
### Scraping / data ([[concepts/scraping-krisha]])
- [[code/scripts/rescrape.py]] — nightly sweep sale/rent (prod path)
- [[code/scripts/crawl.py]] — legacy one-shot crawler (Makefile)
- [[code/scripts/crawl_complexes.py]] — ЖК catalogue → `models/complexes.json`
- [[code/scripts/fetch_osm_pois.py]] — OSM POIs → `models/osm_pois.json`
- [[code/scripts/fetch_osm_zones.py]] — OSM zones → `models/osm_zones.json`
- [[code/scripts/backfill_gap_cohort.py]] — one-off rent DB cohort repair (issue #156)
- [[code/scripts/analyze_flags.py]] — Gemini description flags → `llm_flags` table
### ML ([[concepts/sale-price-model]], [[concepts/rent-model]])
- [[code/scripts/train.py]] — train sale/rent model
- [[code/scripts/model_gate.py]] — metric gate (blocks publish)
- [[code/scripts/notify_retrain.py]] — Telegram retrain report
- [[code/scripts/sync_readme_metrics.py]] — README METRICS block from meta
- [[code/scripts/backtest.py]] — walk-forward bench (manual, issue #130)
- [[code/scripts/compare_models.py]] — all eras on one test → `reports/model_comparison.json`
- [[code/scripts/dedup_stats.py]] — dedup stats w/o training
### Ops / release ([[concepts/state-storage]], [[concepts/prod-runtime]])
- [[code/scripts/release_upload.py]] — crash-safe GitHub release upload (db-latest, model-latest)
- [[code/scripts/publish_snapshot.py]] — dated `snapshot-YYYY-MM-DD` releases + rotation
- [[code/scripts/restore_drill.py]] — weekly backup restore drill
- [[code/scripts/smoke_prod.py]] — prod smoke
- [[code/scripts/health_check.py]] — /api/health → Telegram on state change
- [[code/scripts/send_alerts.py]] — post-rescrape Telegram fan-out ([[concepts/telegram-bot]])
- [[code/scripts/loadtest.py]] — latency/throughput measurement (manual)
- [[code/scripts/gen_runtime_lock.py]] — `requirements-runtime.lock` generator

## Workflow → scripts
- [[ops/workflows/rescrape]] — rescrape.py → release_upload.py (sweep state, DB) → publish_snapshot.py → send_alerts.py → release_upload.py (DB again)
- [[ops/workflows/rescrape-rent]] — rescrape.py --deal arenda → release_upload.py → publish_snapshot.py → send_alerts.py --deal arenda
- [[ops/workflows/retrain]] — train.py (--compare-old) → model_gate.py → notify_retrain.py (always) → release_upload.py (model-latest + model-<date>) → sync_readme_metrics.py → git commit `models/ reports/ README.md`; then same chain for rent
- [[ops/workflows/restore-drill]] — restore_drill.py, restore_drill.py --rent
- [[ops/workflows/smoke]] — smoke_prod.py
- [[ops/workflows/space-keepalive]] — health_check.py
- [[ops/workflows/backfill-gap-cohort]] — backfill_gap_cohort.py → release_upload.py
- [[ops/workflows/ci]] — none directly; `tests/test_runtime_lock.py` etc. import scripts
- not run by any workflow: crawl, crawl_complexes, fetch_osm_pois, fetch_osm_zones, analyze_flags, backtest, compare_models, dedup_stats, loadtest, gen_runtime_lock (Makefile `lock`)
- `Makefile` targets: lock (gen_runtime_lock), crawl (2 variants), complexes, rescrape, train

## Gotchas
- ops scripts deliberately avoid project deps (stdlib/httpx/`gh`) so workflows skip installing catboost
- release writes always via release_upload.py, never `gh release upload --clobber` (audit 24.10)
- several scripts were dead code until wired (health_check, restore_drill) — check a workflow actually calls a script before trusting it runs
