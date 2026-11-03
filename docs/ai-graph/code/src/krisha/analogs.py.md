---
type: file
path: src/krisha/analogs.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/analogs.py
purpose: «Похожие квартиры»: kNN among active listings (same rooms, area ±25%) ranked by weighted area/geo/year distance.
concepts: [[concepts/web-api]]
## Key symbols
- `MAX_ANALOGS` — 5 `src/krisha/analogs.py:21`
- `AREA_TOLERANCE` — 0.25 `src/krisha/analogs.py:22`
- `find_analogs` — subject → top-k analog dicts; errors → [] (fail-soft) `src/krisha/analogs.py:61`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
## Used by
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/predict.py]]
- tests: [[code/tests/_dir|tests/test_analogs.py]]
