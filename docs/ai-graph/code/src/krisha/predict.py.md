---
type: file
path: src/krisha/predict.py
tags: [zone/src, layer/ml, layer/api]
updated: 2026-11-03
---
# src/krisha/predict.py
purpose: Inference: listing URL/dict → fair price, CQR interval, verdict, SHAP factors with hints, details, market signals, rental yield, scam badge, analogs; also batch pricing for alerts.
concepts: [[concepts/sale-price-model]], [[concepts/rent-model]], [[concepts/price-interval-cqr]], [[concepts/web-api]]
## Key symbols
- `USER_COOKIES` — process-wide StickyCookies for user path `src/krisha/predict.py:48`
- `InvalidListingUrl` — user error → 422 `src/krisha/predict.py:50`
- `ListingNotFound` — krisha 404 → 404 `src/krisha/predict.py:60`
- `ListingOutsideAlmaty` — ValueError subclass → 422 `src/krisha/predict.py:70`
- `DailyRentNotSupported` — посуточная аренда → 422 `src/krisha/predict.py:84`
- `KRISHA_URL_RE` — `krisha\.kz/a/show/(\d+)` `src/krisha/predict.py:92`
- `VERDICT_THRESHOLD` — 0.10 legacy flat threshold when no interval model `src/krisha/predict.py:94`
- `load_model` — deal → (CatBoostRegressor, meta) from models/ or models/rent/ `src/krisha/predict.py:197`
- `load_interval_models` — lru_cache; MultiQuantile model or legacy model_lo/hi pair (sale only) or None `src/krisha/predict.py:227`
- `_verdict_interval` — GOOD_DEAL if price < low, OVERPRICED if > high, else FAIR `src/krisha/predict.py:268`
- `FACTOR_GROUPS` — collinear features merged into one factor for SHAP display `src/krisha/predict.py:286`
- `explain_price` — SHAP → (top_factors, factors_base, factors_other) in ₸ `src/krisha/predict.py:404`
- `_apply_cqr` — new meta `cqr_scale` (width-proportional) or legacy `cqr_offset_log` `src/krisha/predict.py:447`
- `_price_pool` — single formula for card and batch: expm1(point), interval, verdict `src/krisha/predict.py:476`
- `predict_from_listing` — dict → full result; opens conn on the deal's DB if none `src/krisha/predict.py:529`
- `_predict_from_listing` — assembles card: factors, details, location, price history, liquidity, rental_yield, scam_risk, renovation (flag), analogs, log_prediction `src/krisha/predict.py:559`
- `predict_listings_batch` — alerts: one DataFrame + one CatBoost call per batch; no SHAP; doesn't write predictions `src/krisha/predict.py:724`
- `user_client` — PoliteClient for user path: 3 attempts, delays 0.5–1.0 s, raise_on_challenge, sticky cookies `src/krisha/predict.py:763`
- `is_almaty` — address.city, else bbox, else True `src/krisha/predict.py:793`
- `predict_from_url` — SSRF-safe: rebuild canonical URL from id; scrape → parse → Almaty/daily-rent checks → predict → duplicate check → upsert source='user' `src/krisha/predict.py:922`
## Depends on
- [[code/src/krisha/analogs.py]]
- [[code/src/krisha/complexes.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/factor_hints.py]]
- [[code/src/krisha/features.py]]
- [[code/src/krisha/geo.py]]
- [[code/src/krisha/interval.py]]
- [[code/src/krisha/market.py]]
- [[code/src/krisha/rent.py]]
- [[code/src/krisha/rental_yield.py]]
- [[code/src/krisha/scam.py]]
- [[code/src/krisha/scraping/client.py]]
- [[code/src/krisha/scraping/detail_parser.py]]
- [[code/src/krisha/spatial.py]]
- [[code/src/krisha/stats.py]]
- [[code/src/krisha/vision.py]]
- [[code/src/krisha/zones.py]]
## Used by
- [[code/src/krisha/alerts.py]]
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/predict_gate.py]]
- [[code/src/krisha/rental_yield.py]]
- [[code/src/krisha/text_parse.py]]
- tests: [[code/tests/_dir|tests/test_alerts_batch.py]], [[code/tests/_dir|tests/test_almaty_only.py]], [[code/tests/test_api_audit_2026_10_24.py]], [[code/tests/_dir|tests/test_bot.py]], [[code/tests/_dir|tests/test_feature_flags.py]], [[code/tests/_dir|tests/test_home_redesign.py]], [[code/tests/_dir|tests/test_interval.py]], [[code/tests/test_model_artifacts.py]], [[code/tests/_dir|tests/test_predict.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_predict_edge_listings.py]], [[code/tests/_dir|tests/test_rent_serving.py]], [[code/tests/_dir|tests/test_runtime_optimizations.py]], [[code/tests/_dir|tests/test_session_warmup.py]], [[code/tests/_dir|tests/test_ux_audit_fixes.py]]
## Data / env
- Reads models/*.cbm + meta (+ models/rent/), spatial_ref, OSM snapshots; SQLite data/krisha.db or data/krisha_rent.db (per detected deal). Outbound: krisha.kz via user_client.
## Gotchas
- Deal (sale/rent) detected per listing via [[code/src/krisha/rent.py]] → different model, DB and price bounds.
- Room-share rent listings: verdict and diff_pct set to None, no scam badge.
- Rent prices rounded to 1k ₸, sale to 10k ₸ (`_round_price`).
- LLM description flags removed from user path in #157; vision only behind FEATURE_VISION.
- Legacy `cqr_offset_log` meta must keep working (old published models) — don't treat missing cqr_scale as 0.
- User upsert never writes price history and never revives delisted lots; result carries `delisted_at` from our DB.
