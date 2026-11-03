---
type: file
path: src/krisha/scraping/rescrape.py
tags: [zone/src, layer/scraping, layer/data]
updated: 2026-11-03
---
# src/krisha/scraping/rescrape.py
purpose: Daily sweep (roadmap stage 4): walk 32 search shards (8 districts × 1/2/3/4+ rooms), record sightings/prices/delists, fetch a quota-limited queue of detail pages, write pass stats.
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `sweep` — sweep(max_pages=250, max_new_details, db_path, deal='prodazha'|'arenda', refresh_stale_days, max_refresh, time_budget_min=320, mode='auto', fresh_share=0.5) → stats dict `src/krisha/scraping/rescrape.py:394`
- `shard_urls` — 32 (label, url) pairs: `{BASE_URL}/{deal}/kvartiry/{district_slug}/?das[live.rooms][]=N` `src/krisha/scraping/rescrape.py:304`
- `_sweep_shard` — paginate one shard → ({id: price}, covered, ban, pages); partial pages survive bans/errors but shard is 'not covered' `src/krisha/scraping/rescrape.py:323`
- `_looks_like_antibot` — checks `_ANTIBOT_SIGNS` on first page of a shard `src/krisha/scraping/rescrape.py:298`
- `_ANTIBOT_SIGNS` — challenge-only markers; must NOT contain bare 'captcha'/'recaptcha' `src/krisha/scraping/rescrape.py:120`
- `DELIST_AFTER_DAYS` — 3 — not seen in search N days → is_active=0 `src/krisha/scraping/rescrape.py:89`
- `DETAIL_POISON_ATTEMPTS` — 3 — failed detail fetches before shard cursor may skip a lot `src/krisha/scraping/rescrape.py:95`
- `MAX_DELIST_SHARE` — 0.30 — mass-delist guard (normal delist 1–4%/day) `src/krisha/scraping/rescrape.py:151`
- `ACTIVE_IN_DB_DROP_RATIO` — 0.5 — found_in_search < 50% of active-in-DB → suspicious (issue #97) `src/krisha/scraping/rescrape.py:144`
- `DEFAULT_TIME_BUDGET_MIN` — 320 — soft wall-clock deadline leaving time for DB upload `src/krisha/scraping/rescrape.py:157`
- `RECOVERY_GAP_DAYS` — 2.5 — observation gap after which the pass is a recovery pass and new first_seen rows are marked as gap cohort (issue #156) `src/krisha/scraping/rescrape.py:175`
- `STARVED_SHARD_STREAK` — 3 — shard with backlog and zero quota N passes → stats['starved_shards'] (issue #168) `src/krisha/scraping/rescrape.py:192`
- `_alert_ban` — Telegram alert to TG_ADMIN_CHAT_ID on early abort `src/krisha/scraping/rescrape.py:202`
## Depends on
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/monitoring.py]]
- [[code/src/krisha/scraping/client.py]]
- [[code/src/krisha/scraping/detail_parser.py]]
- [[code/src/krisha/scraping/listing_parser.py]]
- [[code/src/krisha/scraping/pass_plan.py]]
- [[code/src/krisha/scraping/shard_plan.py]]
- [[code/src/krisha/validity.py]]
## Used by
- [[code/scripts/rescrape.py]]
- [[ops/workflows/rescrape]]
- [[ops/workflows/rescrape-rent]]
- tests: [[code/tests/_dir|tests/test_drain_mode.py]], [[code/tests/test_rescrape.py]], [[code/tests/_dir|tests/test_rescrape_safeline_streak.py]], [[code/tests/test_shard_plan.py]]
## Data / env
- Reads/writes SQLite `data/krisha.db` or `data/krisha_rent.db` (tables listings, price_history, listing_shards, shard_cursors, sweep_runs, sweep_shard_stats, sweep_state, data_gaps, detail_fetch_failures, parse_anomalies).
- Local file `data/rescrape_history_<deal>.json` (parse-rate history; only useful on long-lived machines, not in Actions).
- Env: `TG_ADMIN_CHAT_ID` (alerts), `KRISHA_DELAY_MIN/MAX` (explicit pause override beats mode preset; empty string = not set), `TELEGRAM_BOT_TOKEN` via bot.tg_call.
## Gotchas
- Regression 14.07.2026: bare 'recaptcha' matched krisha's footer `g-recaptcha-policy` on every page → all shards 'captcha', found_in_search=0, workflow failed 13 days. Keep only challenge markers.
- Delist only when ALL shards covered; truncated (deeper than max_pages), empty-first-page, antibot or banned shards are 'not covered' (issue #96).
- Mass-delist guard denominator = active_in_db BEFORE the pass; first pass after a long blind period may legitimately trip it — do not raise thresholds (comment in code).
- Detail queue (title IS NULL & is_active=1) is split by largest-remainder quotas proportional to measured shard stock (issue #166); undershoot is NOT given to neighbours; per-shard cursor in shard_cursors; fresh lots (<48h, above cursor) get `fresh_share` of the window and don't move the cursor (issue #190).
- Cursor stops at the first failed lot of the sequential window (audit 03.10/24.10) — fixed bug where a later success skipped a failed lot until wrap-around.
- pass_seq is incremented at pass START in a separate transaction (issue #168) so a hard-killed runner doesn't repeat the same rotation offset.
- Card price goes through the same price data-contract as upsert (issue #103); detail refetch must not resurrect delisted lots (handled in db UPSERT_SQL).
- Mode drain/steady chosen from backlog with hysteresis (see [[code/src/krisha/scraping/pass_plan.py]]); 2 consecutive banned passes → steady delays (BAN_ROLLBACK_STREAK).
