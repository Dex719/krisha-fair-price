---
type: file
path: src/krisha/forecast.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/forecast.py
purpose: Naive 3/6-month ₸/m² forecast per city/district: OLS on weekly medians (behind FEATURE_FORECAST, off by default).
concepts: [[concepts/web-api]]
## Key symbols
- `HORIZONS` — {'m3': 13, 'm6': 26} weeks `src/krisha/forecast.py:25`
- `MIN_POINTS` — 6; MIN_WEEKS_M6 12; MIN_N_DISTRICT 30 `src/krisha/forecast.py:22`
- `build_forecast` — {city, districts, disclaimer} `src/krisha/forecast.py:78`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/stats.py]]
## Used by
- [[code/src/krisha/api/app.py]]
- tests: [[code/tests/_dir|tests/test_forecast.py]]
## Gotchas
- Calendar-gap slope bug fixed in #109.
