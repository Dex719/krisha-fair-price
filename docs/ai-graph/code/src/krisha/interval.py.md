---
type: file
path: src/krisha/interval.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/interval.py
purpose: finalize_interval shared by train and predict so gate coverage measures exactly the interval users see (issue #105).
concepts: [[concepts/price-interval-cqr]]
## Key symbols
- `MIN_INTERVAL_WIDTH_LOG` — 0.05 minimum width in log space used by CQR `src/krisha/interval.py:19`
- `finalize_interval` — swap if low>high, widen to contain point; never narrows; scalar or ndarray `src/krisha/interval.py:22`
## Used by
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/train.py]]
- tests: [[code/tests/_dir|tests/test_interval.py]], [[code/tests/test_model_artifacts.py]]
