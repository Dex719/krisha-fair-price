---
type: file
path: src/krisha/scam.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/scam.py
purpose: «Подозрительно дёшево» badge (issue #157): price below CQR-calibrated lower bound; level high only if ≥15% below AND fresh.
concepts: [[concepts/price-interval-cqr]]
## Key symbols
- `DEEP_BELOW_PCT` — 15.0 `src/krisha/scam.py:35`
- `FRESH_DAYS` — 7 `src/krisha/scam.py:39`
- `assess_scam_risk` — (fair_low, actual_price, days_on_market) → {level, below_pct, reasons} | None `src/krisha/scam.py:42`
## Used by
- [[code/src/krisha/alerts.py]]
- [[code/src/krisha/predict.py]]
- tests: [[code/tests/_dir|tests/test_scam.py]]
## Gotchas
- Replaced an ad-hoc score system (regexes on «задаток», photo counts) that flagged honest listings.
