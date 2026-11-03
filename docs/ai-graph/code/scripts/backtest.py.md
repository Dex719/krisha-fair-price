---
type: file
path: scripts/backtest.py
tags: [zone/scripts, layer/ml, layer/research]
updated: 2026-11-03
---
# scripts/backtest.py
purpose: issue #130 walk-forward bench — N weekly folds back in time on DB history; honest model/pipeline comparison. Manual, run on pipeline changes (not weekly, not in any workflow).
## Method
- fold with test-week start T: train = `first_seen < T - gap`, price = last `price_history` point strictly before `T - gap`; test = `first_seen in [T, T+7d)`, price as of T+7d
- SAME code as prod (since 10.2026): `krisha.train.prepare_frame`, `fit_point_model` (early stopping), `fit_quantile_interval` + `interval_bounds` (CQR). Before 10.2026 bench had its own pipeline copy that diverged (no `resolve_zones`, 600 trees w/o early stopping) → conclusions "feature didn't help" from old bench should be rechecked
- purge by fingerprint: removes train rows whose fingerprint reappears in test
- `MIN_VALID_FOLDS=3` (issue #158), `FOLD_WINDOW_DAYS=7`, `N_FOLDS_DEFAULT=8`; folds validated by `krisha.validity.representativeness`
## CLI (`main()` ~:735)
- `--db` (default: DB of chosen deal), `--deal prodazha|arenda`, `--label current`, `--out reports/backtest`
- `--n-folds 8`, `--window-days 7`, `--point-iterations`, `--quantile-iterations`, `--learning-rate 0.05`, `--depth 8`, `--seed 42`
- `--cb-param KEY=JSON` (repeatable, any CatBoost param), `--no-interval` (~40% faster), `--skip-invalid-folds`, `--include-invalid` (for --compare)
- `--gap-days 0` (14 = like prod), `--target-mode` (`krisha.targets.TARGET_MODES`, issue #131), `--freshness-half-life-days`, `--train-window-weeks`
- `--compare CSV_A CSV_B` — paired compare of two `*_predictions.csv` instead of a run
## Outputs
- `<out>/<label>_predictions.csv`, `<label>_report.json`, `<label>_report.md`; compare → `<out>/compare_<a>_vs_<b>.md`
- committed historical outputs: `reports/backtest_132/`, `reports/target_experiment_131/` (see [[code/reports/_dir]])
## Depends on
- [[code/src/krisha/config.py]] (`REPORTS_DIR`), [[code/src/krisha/features.py]] (`build_features`), [[code/src/krisha/model_spec.py]], [[code/src/krisha/targets.py]], [[code/src/krisha/train.py]] (prepare_frame, fit_point_model, fit_quantile_interval, interval_bounds, dedup_relistings, purge_leaked_train_rows, baseline_predict, building_groups, `_filter_stale_and_out_of_area`), [[code/src/krisha/validity.py]]
- [[concepts/sale-price-model]], [[concepts/rent-model]]
## Used by
- manual only; tests: `tests/test_backtest.py`
