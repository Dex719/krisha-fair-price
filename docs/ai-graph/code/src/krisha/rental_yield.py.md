---
type: file
path: src/krisha/rental_yield.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/rental_yield.py
purpose: Gross rental yield for a SALE listing: run the rent model on the sale lot (mapped to rent vocabulary, missing rent features from meta feature_defaults) → expected rent + interval, yield = 12×rent/price, plus district benchmark.
concepts: [[concepts/rent-model]]
## Key symbols
- `as_rental` — sale listing → rent-page vocabulary (renovation, toilet, balcony) `src/krisha/rental_yield.py:85`
- `ROUGH_FINISH` — черновая/свободная планировка → estimate 'after renovation' (assumes_renovation) `src/krisha/rental_yield.py:50`
- `estimate` — (listing, sale_price) → dict | None `src/krisha/rental_yield.py:131`
- `district_yield` — 12 × rent ₸/m² / sale ₸/m² medians, last DISTRICT_WINDOW_DAYS=60, min DISTRICT_MIN_N=30 `src/krisha/rental_yield.py:180`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/features.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/spatial.py]]
## Used by
- [[code/src/krisha/predict.py]]
- tests: [[code/tests/_dir|tests/test_rent_serving.py]]
## Data / env
- Rent model + rent spatial_ref; both DBs for district medians (cached 6 h).
## Gotchas
- Gross yield by asking prices: no tax, vacancy, utilities, repairs.
