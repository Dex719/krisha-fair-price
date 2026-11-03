---
type: file
path: src/krisha/rent.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/rent.py
purpose: Rent-specific helpers shared by training and inference: deal detection, daily-rent detection, room-share (подселение) detection/filtering.
concepts: [[concepts/rent-model]]
## Key symbols
- `detect_deal` — explicit sectionAlias → price < 3M → rent param keys → sale param keys → price < 5M → sale `src/krisha/rent.py:56`
- `is_daily_rent` — посуточ/за сутки/почасов/тәулік in title/params, or in description with price < 45k `src/krisha/rent.py:92`
- `ROOM_SHARE_RE` — regex for подселение/койко-место/комната `src/krisha/rent.py:24`
- `is_room_share` — description match `src/krisha/rent.py:105`
- `drop_room_shares` — removes room shares from rent training (~0.7% of base, ~78% error) `src/krisha/rent.py:110`
## Used by
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/train.py]]
- tests: [[code/tests/_dir|tests/test_rent_model.py]], [[code/tests/_dir|tests/test_rent_serving.py]], [[code/tests/_dir|tests/test_ux_audit_fixes.py]]
