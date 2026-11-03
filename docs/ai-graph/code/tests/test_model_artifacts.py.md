---
type: file
path: tests/test_model_artifacts.py
tags: [zone/tests, layer/ml]
updated: 2026-11-03
---
# tests/test_model_artifacts.py
purpose: invariants over REAL files in models/ (model.cbm, model_quantile.cbm, model_meta.json, metrics_history.jsonl, model_gate_samples.json, spatial_ref.json); run as retrain gate.

## Key symbols (tests)
- meta keys/types; meta features == code `ALL_FEATURES/CAT_FEATURES`; model feature names == meta; dedup/split counters consistent
- quantile model consistent with main; `load_interval_models` prefers real multiquantile; quantiles ordered; interval chain valid
- model beats baseline; not worse than old within gate tolerance
- metrics_history lines valid/ordered, last == meta; gate samples contract
- sanity: plausible Almaty prices; bigger area costs more; spatial_ref contract; ppsm maps in meta sane (PPSM_MIN/MAX)

## Depends on
- [[code/src/krisha/predict.py]], [[code/src/krisha/features.py]], [[code/src/krisha/interval.py]], [[code/src/krisha/spatial.py]], [[code/src/krisha/config.py]], [[code/scripts/model_gate.py]] (via sys.path scripts/)

## Used by
- [[ops/workflows/retrain]] (`pytest -q tests/test_model_artifacts.py`); rent twin: tests/test_rent_model_artifacts.py. [[concepts/sale-price-model]]

## Gotchas
- Whole module skips on fresh clone without artifacts (weights in private release, [[concepts/state-storage]]).
- Only ranges/invariants, never exact metrics — weekly retrain must not break it.
