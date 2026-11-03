---
type: file
path: src/krisha/market.py
tags: [zone/src, layer/ml, layer/data]
updated: 2026-11-03
---
# src/krisha/market.py
purpose: Market signals for the card (roadmap stage 4): price history points, days on market, liquidity («срок продажи») from delisted similar listings.
concepts: [[concepts/web-api]]
## Key symbols
- `MIN_DELISTED_SAMPLE` — 15; MIN_BAND_SAMPLE 20; MIN_CITY_SAMPLE 30; NEAR_BAND_PCT 5.0 `src/krisha/market.py:17`
- `price_history_points` — price_history rows for the chart `src/krisha/market.py:79`
- `days_on_market` — first_seen → last_seen/now `src/krisha/market.py:91`
- `liquidity_estimate` — median days to delist of segment (district+rooms) with price band by diff_pct from predictions log `src/krisha/market.py:167`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
## Used by
- [[code/src/krisha/alerts.py]]
- [[code/src/krisha/predict.py]]
- tests: [[code/tests/_dir|tests/test_market.py]]
## Gotchas
- Liquidity uses only organic rows: is_active=0, delisted_at set, days ≥ 1, `first_seen_cohort IS NULL` (cohorts 'initial' and 'gap:*' are set by rescrape.sweep — their first_seen is our discovery date, not publication) and trusted delist lag ≤ MAX_TRUSTED_DELIST_LAG_DAYS (issue #156).
