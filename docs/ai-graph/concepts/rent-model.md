---
type: concept
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# Rent model and rental yield
purpose: monthly rent (₸/мес) model for long-term rent listings, and its reuse to estimate gross rental yield of sale listings.

## What
- Same pipeline as [[concepts/sale-price-model]] (`krisha.train.train(spec=RENT)`), spec `model_spec.RENT` ([[code/src/krisha/model_spec.py]]): DB data/krisha_rent.db, bounds 20k..10M ₸/мес and 1k..40k ₸/m², features `RENT_NUM_FEATURES`/`RENT_CAT_FEATURES` ([[code/src/krisha/features.py]]: + balcony/loggia counts, toilet count, kitchen-studio, facilities & "who" flags, bathroom, window side…).
- Artifacts in models/rent/ (model.cbm, model_quantile.cbm (MultiQuantile only, no legacy pair), model_meta.json incl. `feature_defaults`, spatial_ref.json, metrics_history.jsonl); archive models_rent.tar.gz in model-latest; retrained in the `retrain-rent` job after sale ([[ops/workflows/retrain]]).
- Data collected by the same sweep with `deal='arenda'` ([[ops/workflows/rescrape-rent]], [[concepts/scraping-krisha]]).

## Pipeline specifics (in order)
1. Training: `rent.drop_room_shares` removes подселение/койко-место listings (~0.7% of base, ~78% error) — [[code/src/krisha/rent.py]].
2. `_typical_values` → meta `feature_defaults` (median/mode of rent-only features).
3. Inference by URL: `detect_deal` (sectionAlias → price < 3M → rent/sale param keys → price < 5M) picks rent model + rent DB; `is_daily_rent` → `DailyRentNotSupported` (422); room share → verdict/diff_pct None, no scam badge; rent spatial ref `RENT_SPATIAL_REF_PATH`; price rounded to 1k ₸.
4. Rental yield for SALE listings ([[code/src/krisha/rental_yield.py]] `estimate`, called from `predict._predict_from_listing`): `as_rental` maps sale vocabulary (renovation/toilet/balcony) to rent, missing features from `feature_defaults`, rough finish → estimate after renovation (`assumes_renovation`); yield = 12 × rent / price; benchmark `district_yield` (medians over last 60 days, n ≥ 30). Fail-soft: no rent model → block absent.
5. Bot `/track` also works for rent lots (checked by evening rescrape-rent → `send_alerts.py --deal arenda`).

## Outputs
- API fields `deal`, `price_period='month'`, `room_share`, `rental_yield` ([[code/src/krisha/api/schemas.py]]); /api/stats/rent ([[code/src/krisha/stats.py]] `compute_rent_stats`); /api/health `rent_model_loaded`, `rent_model_error_pct`.

## Gotchas
- Rent must load its own spatial_ref (bug: it used the sale ref).
- Never validate rent prices with sale bounds (`db.price_bounds_for`).
- Gross yield by asking prices: no tax, vacancy, utilities.
