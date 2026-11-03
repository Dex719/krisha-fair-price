---
type: file
path: src/krisha/config.py
tags: [zone/src, layer/config]
updated: 2026-11-03
---
# src/krisha/config.py
purpose: Central constants: paths (data/, models/, reports/), krisha URLs and shard dimensions, HTTP politeness, data-contract bounds, city center/bbox, feature flags.
## Key symbols
- `ROOT_DIR` — repo root = parents[2] of this file `src/krisha/config.py:7`
- `DB_PATH` — data/krisha.db (sale) `src/krisha/config.py:11`
- `RENT_DB_PATH` — data/krisha_rent.db (rent) `src/krisha/config.py:14`
- `MODEL_PATH` — models/model.cbm; also MODEL_QUANTILE_PATH, MODEL_META_PATH, legacy MODEL_LO/HI_PATH `src/krisha/config.py:15`
- `RENT_MODELS_DIR` — models/rent/ with the same artifact names `src/krisha/config.py:43`
- `SPATIAL_REF_PATH` — models/spatial_ref.json; also OSM_POIS/OSM_ZONES/COMPLEXES snapshots in models/ `src/krisha/config.py:36`
- `SEARCH_URL` — https://krisha.kz/prodazha/kvartiry/almaty/ `src/krisha/config.py:53`
- `ALMATY_DISTRICT_SLUGS` — 8 districts RU name → krisha slug (shard dimension 1) `src/krisha/config.py:61`
- `ROOM_SHARDS` — 1к/2к/3к/4к+ → das[live.rooms][] values (shard dimension 2) `src/krisha/config.py:72`
- `DISTRICT_RU` — krisha district code (Almalinskiy_r-n…) → RU name `src/krisha/config.py:82`
- `listing_shard_label` — shard label «Район Nк» from detail fields `src/krisha/config.py:94`
- `_env_float` — float env with empty string = unset (issue #152) `src/krisha/config.py:113`
- `REQUEST_DELAY_RANGE` — (KRISHA_DELAY_MIN=2.0, KRISHA_DELAY_MAX=4.0) `src/krisha/config.py:130`
- `ALMATY_CENTER` — (43.2399, 76.9570) Абая/Достык `src/krisha/config.py:141`
- `LEGACY_ALMATY_CENTER` — (43.2398, 76.8898) — wrong point used before 10.2026; old metas without city_center use it `src/krisha/config.py:146`
- `PRICE_MIN` — sale bounds 5M..1.5B ₸; RENT_PRICE 20k..10M ₸/mo; AREA 10..500 m²; PPSM 100k..5M; RENT_PPSM 1k..40k `src/krisha/config.py:149`
- `ALMATY_BBOX` — lat 42.95–43.50, lon 76.55–77.25 `src/krisha/config.py:167`
- `SHARED_PIN_MIN` — 5 listings on one rounded point = ЖК pin (coords_approx) `src/krisha/config.py:179`
- `STALE_DELISTED_DAYS` — 90 — delisted rows older than this excluded from train `src/krisha/config.py:184`
- `MAX_TRUSTED_DELIST_LAG_DAYS` — 7 — shared by tracking (delist alerts) and market (liquidity) `src/krisha/config.py:201`
- `feature_vision` — env FEATURE_VISION, default off `src/krisha/config.py:224`
- `feature_forecast` — env FEATURE_FORECAST, default off `src/krisha/config.py:230`
## Used by
- [[code/src/krisha/alerts.py]]
- [[code/src/krisha/analogs.py]]
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/channel.py]]
- [[code/src/krisha/complexes.py]]
- [[code/src/krisha/daily_report.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/db_release.py]]
- [[code/src/krisha/factor_hints.py]]
- [[code/src/krisha/features.py]]
- [[code/src/krisha/forecast.py]]
- [[code/src/krisha/geo.py]]
- [[code/src/krisha/llm_flags.py]]
- [[code/src/krisha/market.py]]
- [[code/src/krisha/model_spec.py]]
- [[code/src/krisha/monitoring.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/prediction_log.py]]
- [[code/src/krisha/rental_yield.py]]
- [[code/src/krisha/scraping/client.py]]
- [[code/src/krisha/scraping/crawler.py]]
- [[code/src/krisha/scraping/rescrape.py]]
- [[code/src/krisha/scraping/sweep_state_file.py]]
- [[code/src/krisha/spatial.py]]
- [[code/src/krisha/stats.py]]
- [[code/src/krisha/subscriptions.py]]
- [[code/src/krisha/tracking.py]]
- [[code/src/krisha/train.py]]
- [[code/src/krisha/usage.py]]
- [[code/src/krisha/validity.py]]
- [[code/src/krisha/vision.py]]
- [[code/src/krisha/zones.py]]
- [[code/scripts/analyze_flags.py]]
- [[code/scripts/backtest.py]]
- [[code/scripts/compare_models.py]]
- [[code/scripts/crawl_complexes.py]]
- [[code/scripts/dedup_stats.py]]
- [[code/scripts/fetch_osm_pois.py]]
- [[code/scripts/fetch_osm_zones.py]]
- [[code/scripts/rescrape.py]]
- [[code/scripts/send_alerts.py]]
- tests: [[code/tests/conftest.py]], [[code/tests/_dir|tests/test_almaty_only.py]], [[code/tests/_dir|tests/test_backtest_alignment.py]], [[code/tests/_dir|tests/test_bot.py]], [[code/tests/_dir|tests/test_city_center.py]], [[code/tests/_dir|tests/test_data_contracts.py]], [[code/tests/_dir|tests/test_drain_mode.py]], [[code/tests/_dir|tests/test_feature_flags.py]], [[code/tests/test_model_artifacts.py]], [[code/tests/_dir|tests/test_page_bot.py]], [[code/tests/_dir|tests/test_rent_model.py]], [[code/tests/test_rescrape.py]], [[code/tests/_dir|tests/test_send_alerts_rent.py]], [[code/tests/_dir|tests/test_stats.py]]
## Data / env
- Env: `KRISHA_DELAY_MIN`, `KRISHA_DELAY_MAX`, `FEATURE_VISION`, `FEATURE_FORECAST` (truthy: 1/true/yes/on).
## Gotchas
- Feature flags are functions (read env per call) so Space restart / monkeypatch changes them without reimport.
- Price bounds differ by deal; applying sale bounds to rent once rejected every rent price and froze krisha_rent.db.
