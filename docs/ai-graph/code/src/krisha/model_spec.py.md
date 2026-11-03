---
type: file
path: src/krisha/model_spec.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/model_spec.py
purpose: ModelSpec dataclass: what differs between sale and rent training (DB, price/ppsm bounds, features, artifact paths); one training pipeline for both.
concepts: [[concepts/sale-price-model]], [[concepts/rent-model]]
## Key symbols
- `ModelSpec` — deal, label, db_path, models_dir, model/quantile/meta/spatial_ref/gate_samples/metrics_history/shap paths, num/cat features, price_bounds, ppsm_bounds `src/krisha/model_spec.py:45`
- `SALE` — deal='prodazha', DB_PATH, models/, sale bounds, NUM/CAT_FEATURES `src/krisha/model_spec.py:71`
- `RENT` — deal='arenda', RENT_DB_PATH, models/rent/, rent bounds, RENT_NUM/CAT_FEATURES `src/krisha/model_spec.py:89`
- `spec_for` — deal → spec `src/krisha/model_spec.py:110`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/features.py]]
## Used by
- [[code/src/krisha/train.py]]
- [[code/scripts/backtest.py]]
- [[code/scripts/notify_retrain.py]]
- [[code/scripts/train.py]]
- tests: [[code/tests/_dir|tests/test_rent_model.py]], [[code/tests/_dir|tests/test_rent_model_artifacts.py]], [[code/tests/_dir|tests/test_rent_serving.py]], [[code/tests/_dir|tests/test_train_refit.py]]
## Gotchas
- Separate artifact dir for rent so a rent retrain can never overwrite sale files.
