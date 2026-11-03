---
type: file
path: scripts/train.py
tags: [zone/scripts, layer/ml]
updated: 2026-11-03
---
# scripts/train.py
purpose: thin CLI over `krisha.train.train()` — trains CatBoost sale (default) or rent model; writes `models/` (or `models/rent/`) artifacts + metrics. See [[concepts/sale-price-model]], [[concepts/rent-model]].
## Key symbols
- `main()` — argparse → `spec_for(deal)` → `train(iterations, old_model_path, spec, old_meta_path)`
## CLI
- `--iterations` (default `krisha.train.POINT_ITERATIONS`)
- `--deal prodazha|arenda` (default prodazha; choices = `model_spec.SPECS`)
- `--compare-old PATH` — old `model.cbm`, evaluated on the NEW test split → `metrics.old_model` in meta (honest gate input)
- `--compare-old-meta PATH` — old `model_meta.json`, so old-model features are built as at its training (city-center point)
## Depends on
- [[code/src/krisha/train.py]], [[code/src/krisha/model_spec.py]]
## Used by
- [[ops/workflows/retrain]] — sale: `train.py --compare-old /tmp/old_model.cbm --compare-old-meta /tmp/old_meta.json`; rent: same with `--deal arenda` (or plain `--deal arenda` when no old model)
- `Makefile` target `train`
- next steps in workflow: [[code/scripts/model_gate.py]] → [[code/scripts/notify_retrain.py]] → [[code/scripts/release_upload.py]] → [[code/scripts/sync_readme_metrics.py]]
## Data / env
- reads `data/krisha.db` / `data/krisha_rent.db` (SQLite; see [[code/data/_dir]])
- writes `models/model.cbm`, `models/model_quantile.cbm`, `models/model_meta.json`, `models/spatial_ref.json`, `models/model_gate_samples.json`, appends `models/metrics_history.jsonl`, `reports/shap_summary.png` (rent: `models/rent/*`, `reports/shap_summary_rent.png`) — see [[code/models/_dir]], [[code/reports/_dir]]
