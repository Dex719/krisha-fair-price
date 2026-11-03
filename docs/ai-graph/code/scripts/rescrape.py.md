---
type: file
path: scripts/rescrape.py
tags: [zone/scripts, layer/scraping]
updated: 2026-11-03
---
# scripts/rescrape.py
purpose: CLI of the regular krisha.kz sweep (roadmap stage 4): price history, days-on-market, new listings, detail refresh. Iterates search by 32 shards "district × rooms". Wraps `krisha.scraping.rescrape.sweep()`. See [[concepts/scraping-krisha]].
## CLI (`main()` :24)
- `--pages 250` — max search pages per shard
- `--mode auto|drain|steady` (issue #152; auto = chosen by DB backlog with hysteresis)
- `--max-new N` — max new detail pages; default from mode (drain 4500 / steady 1500); 0 = diagnostic pass
- `--refresh-stale-days N` (issue #102; drain 30 / steady 45), `--max-refresh N` (drain 800 / steady 1200; 0 = off)
- `--fresh-share 0.5` (issue #190: share of each shard's detail window for lots first seen in last 48 h)
- `--deal prodazha|arenda` → `DB_PATH` (`data/krisha.db`) or `RENT_DB_PATH` (`data/krisha_rent.db`, price = ₸/month)
- `--time-budget-min 320` — soft deadline; stops gracefully before the runner's hard `timeout-minutes` kills the upload step
- `--summary-json FILE` — dump sweep counters (`found_in_search`, `banned`, `banned_phase`, `suspicious`, `parse_rate_median_7`, …)
- `--fail-empty` — exit 1 if search empty, banned (403 series) outside details phase, or `suspicious` (parse-rate drop vs median of last 7 runs). Ban during `details` phase → logs error but exit 0 (search fully covered, DB still uploaded)
- `--fail-below N` — exit 1 if `found_in_search < N`
## Depends on
- [[code/src/krisha/config.py]] (`DB_PATH`, `RENT_DB_PATH`), [[code/src/krisha/scraping/rescrape.py]] (`sweep`)
## Used by
- [[ops/workflows/rescrape]] (cron `0 1 * * *` = 06:00 Almaty): `--pages ${INPUT_PAGES:-250} [--max-new] --time-budget-min ${INPUT_TIME_BUDGET_MIN:-300} --fail-empty --fail-below 8000 --summary-json /tmp/rescrape_stats.json`
- [[ops/workflows/rescrape-rent]] (cron `0 15 * * *` = 20:00 Almaty): `--deal arenda --max-new ${INPUT_MAX_NEW:-1000} --time-budget-min 300 --fail-empty --fail-below 2000 --summary-json /tmp/rescrape_rent_stats.json`
- `Makefile` target `rescrape`; tests `tests/test_rescrape_cli.py`, `tests/test_drain_mode.py`, `tests/test_rescrape_workflow.py`
- downstream steps in same job: [[code/scripts/release_upload.py]] (db-latest), [[code/scripts/publish_snapshot.py]], [[code/scripts/send_alerts.py]]
## Data / env
- writes SQLite DB (listings, price_history, sweep_runs, …) → uploaded to [[concepts/state-storage]]; source host krisha.kz behind SafeLine WAF
## Gotchas
- 2026-09-20 job died by `timeout-minutes` on the Alerts step → soft budget + post-step reorder (see [[history/kiro-specs]], spec rescrape-post-steps)
