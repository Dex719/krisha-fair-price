---
type: dir
path: src/krisha/scraping
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# src/krisha/scraping
purpose: Everything that talks to krisha.kz HTML: polite HTTP client with SafeLine/ban handling, page parsers, the production daily sharded sweep and its pure planners. `__init__.py` is empty. Concept: [[concepts/scraping-krisha]].
## Files
- [[code/src/krisha/scraping/client.py]] — PoliteClient, BanDetected/SourceUnavailable/ChallengeBlocked, StickyCookies.
- [[code/src/krisha/scraping/json_window.py]] — robust `window.<x> = {...}` extraction.
- [[code/src/krisha/scraping/detail_parser.py]] — listing page → listing dict.
- [[code/src/krisha/scraping/listing_parser.py]] — search page → ids, card prices, next page.
- [[code/src/krisha/scraping/complex_parser.py]] — ЖК page → complexes row.
- [[code/src/krisha/scraping/rescrape.py]] — `sweep()` (production).
- [[code/src/krisha/scraping/pass_plan.py]] — drain/steady modes, time budget (pure).
- [[code/src/krisha/scraping/shard_plan.py]] — per-shard quotas, rotation (pure).
- [[code/src/krisha/scraping/sweep_state_file.py]] — pass_seq/ban_streak sidecar JSON in release.
- [[code/src/krisha/scraping/crawler.py]] — legacy unsharded crawler (`scripts/crawl.py`).
## Main data flow
[[code/scripts/rescrape.py]] → `sweep()` → for each of 32 shards `_sweep_shard` (client.get → listing_parser) → `db.record_sighting` / price_history / `record_listing_shards` → quotas (shard_plan) → detail fetch (client.get → detail_parser → `db.upsert_listing`) → refresh stale details → delist (guards) → `record_sweep_run` → summary JSON → [[code/src/krisha/daily_report.py]].
## Used by (outside package)
- User path also uses client + detail_parser: [[code/src/krisha/predict.py]] (`user_client`), [[code/src/krisha/bot.py]], [[code/src/krisha/predict_gate.py]], [[code/src/krisha/api/app.py]] (exception classes).
