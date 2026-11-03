---
type: file
path: scripts/compare_models.py
tags: [zone/scripts, layer/ml, layer/research]
updated: 2026-11-03
---
# scripts/compare_models.py
purpose: evaluate ALL historical model checkpoints (git eras) on ONE fresh test split — extension of the gate methodology ([[code/scripts/model_gate.py]]) from old-vs-new to N eras. Manual.
## Method
- frame of anchor era (last retrain commit; meta = `models/model_meta.json`): `load_dataset_asof()` :125 — same filters as `load_dataset` (source!="user", stale, bbox) with "now" frozen at `metrics.trained_at` and `first_seen <= trained_at`
- then prod pipeline: clean → resolve_zones → dedup_relistings → time_based_split → purge
- features identical for all eras: maps of anchor train slice (pre-refit: `ppsm_maps` from meta + `models/spatial_ref.json`; post-refit 10.2026: recomputed on train part, since meta maps include test)
- anchor validation mandatory: last era (`metrics.model`), previous (`metrics.old_model`), baseline must reproduce within `ANCHOR_TOLERANCE=0.004` :74 → else exit 1. After refit `metrics.model` anchor skipped; eras that saw part of test excluded with reason
- `KNOWN_TRANSFORMS={"log1p"}` :113
## Key symbols
- `load_dataset_asof` :125, `era_data_cutoff` :159, `evaluate_era` :168, `_prev_anchor_on_unseen` :235, `to_markdown` :262, `main` :286
## CLI
- `--db` (default `DB_PATH`; prefer frozen copy), `--eras-dir` (required; `<dir>/<commit>/model.cbm [+ model_meta.json]`), `--meta` (default `MODEL_META_PATH`), `--spatial-ref` (default `SPATIAL_REF_PATH`), `--out` (default `reports/model_comparison.json`)
## Outputs
- `reports/model_comparison.json` + markdown table on stdout (numbers: [[code/reports/_dir]])
## Depends on
- [[code/src/krisha/config.py]], [[code/src/krisha/features.py]] (`CAT_FEATURES`, `build_features`, `model_center`, `clean`), [[code/src/krisha/spatial.py]] (`self_indices_for`), [[code/src/krisha/train.py]], [[code/src/krisha/zones.py]] (`resolve_zones`)
- [[concepts/sale-price-model]]
## Used by
- manual; tests `tests/test_compare_models_refit.py`
## Gotchas
- Windows console cp1251 can break printing ₸/²; handled so exit code of written report is not replaced
