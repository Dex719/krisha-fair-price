---
type: file
path: scripts/model_gate.py
tags: [zone/scripts, layer/ml, layer/ops]
updated: 2026-11-03
---
# scripts/model_gate.py
purpose: metric gate of weekly retrain — exit 1 if new model is worse than old beyond tolerance; workflow then does NOT publish the new model (prod keeps old). Part of [[concepts/sale-price-model]] / [[concepts/rent-model]] lifecycle.
## Key symbols
- constants `scripts/model_gate.py:53-91`: `MAPE_TOLERANCE=0.005` (+0.5 pp abs, fallback when no bootstrap), `MAE_TOLERANCE_REL=0.05`, `COVERAGE_TOLERANCE=0.02`, `WIDTH_TOLERANCE_REL=0.10`, `BOOTSTRAP_TOLERANCE=0.0015` (+0.15 pp, cluster bootstrap), `BOOTSTRAP_TOLERANCE_ROWWISE=0.003`, `BOOTSTRAP_N=2000`, `BOOTSTRAP_CI=0.90` (two-sided, lower bound checked), `HISTORY_WINDOW=4`, `HISTORY_TOLERANCE_ABS=0.005`, `HISTORY_TOLERANCE_REL=0.06`
- `bootstrap_ape_diff()` :94 — paired bootstrap of mean APE diff new−old, resampling whole buildings (clusters)
- `load_history()` :137, `history_check()` :162 — soft anti-drift check vs best MAPE of last 4 `metrics_history.jsonl` rows
- `main()` :180
## CLI
- positional `old_meta new_meta`
- `--samples` (default `models/model_gate_samples.json`; paired per-row APE + `clusters`, written by train)
- `--history` (default: `metrics_history.jsonl` next to new_meta)
- `--summary FILE` — markdown report (GITHUB_STEP_SUMMARY)
## Modes
- honest: `new_meta.metrics.old_model` present (train ran `--compare-old`) → compare on the same test split
- fail-closed (issue #106): `old_model_error` present → block publication, no blind fallback
- fallback: neither present → compare with old meta metrics (different test weeks, wider tolerances)
- also gates interval `coverage_test` and width; old width from `metrics.old_model_interval` (same test)
- every new field optional → softer legacy mode + warning (methodology change must not block forever)
## Depends on
- stdlib + numpy only (no src imports)
## Used by
- [[ops/workflows/retrain]] — sale: `model_gate.py /tmp/old_meta.json models/model_meta.json --summary $GITHUB_STEP_SUMMARY`; rent: `... models/rent/model_meta.json --samples models/rent/model_gate_samples.json`
- tests: `tests/test_model_gate.py`
## Gotchas
- audit 24.10 (PR #244): cluster bootstrap because ~65% of test = twin apartments/relistings; tolerance tightened 0.5→0.15 pp; history check added against slow weekly creep
- [[code/scripts/compare_models.py]] extends this methodology to all historical eras
