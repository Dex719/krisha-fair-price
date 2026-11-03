---
type: dir
path: reports/
tags: [zone/reports, layer/ml, layer/research]
updated: 2026-11-03
---
# reports/
purpose: committed evaluation outputs and SHAP plots. `config.REPORTS_DIR`. `.gitignore` ignores only `reports/shap_summary_rent.png` here (side product of rent training, not used in README); everything else is committed by hand or by retrain. Untracked locally (2026-11-03, not committed): `feature_importance_prod.png`.

## Tracked files
- `shap_summary.png` — global SHAP summary of sale model; written by `krisha.train` (`model_spec.SALE.shap_path`) on each retrain, committed by [[ops/workflows/retrain]]; linked from README. Rent analogue `shap_summary_rent.png` (untracked)
- `model_comparison.json` — output of [[code/scripts/compare_models.py]] (asof 2026-08-16)
- `backtest_132/` — issue #132 (MultiQuantile vs two quantile models), output of [[code/scripts/backtest.py]] `--label before|after` + `--compare`
- `target_experiment_131/` — issue #131 target-mode experiment (A…I runs) of [[code/scripts/backtest.py]]

## model_comparison.json (reports/model_comparison.json)
- asof 2026-08-16T07:01Z, test window 2026-08-01..08-16, n_test 5434; caveat: 75% of test lots came in 3 densest days; `temporal_validity=false`, time_confounding worst day TVD 0.745 → relative ranking honest, absolute MAPE not typical
- anchors ok: 974c893 expected 0.075763 vs actual 0.075051; c244821 0.078931 vs 0.078125; baseline 0.165962 vs 0.16484 (tolerance 0.004)
- era MAPE on common test: fd06da4 06-12 first model 23 feats 0.1019 · 29cf732 0.1020 · b160ff7 +raw_params 32 feats 0.0980 · 54a3752 +ЖК catalogue 36 feats 0.0963 · b26a657 +OSM POI 45 feats 0.0957 · c32ed2b 07-02 +H3, honest CV, 47 feats 0.0956 · e7c837e 08-02 time holdout+purge 0.0841 · c244821 08-09 0.0781 · 974c893 08-16 0.0751 · baseline 0.1648
- excluded: 700ba4b (same artifact as c32ed2b, only added CQR)

## backtest_132 (synthetic DB, 3000 listings, 20 weeks, 6 folds, 898 paired rows)
- reports/backtest_132/compare_before_vs_after.md: MAPE 10.63% both, MdAPE 4.97% both; coverage 79.73% → 80.18%; pinball q10 659 244 → 689 574; q90 1 173 099 → 1 230 239; median width 13.38% → 14.12%
- after_report.md: bias +2.61%; per-fold MAPE 9.83–11.33%, coverage 74.24–87.41%
- verdict: MultiQuantile ok (coverage not worse) at small pinball cost; recommended re-run on real DB

## target_experiment_131 (synthetic, 6000 listings, 70 weeks, drift +0.6%/week, 8 folds)
- reports/target_experiment_131/README.md: A `price` MAPE 18.19% bias −18.19%; B `ppsm` 18.27%; C `index_residual` 9.52% bias −0.50% width 26.36%
- add-ons: D index_residual+hl90 9.31% (best); E +win26 9.42%; F +win52 9.40%; G price+hl90 11.33%; H price+win26 8.40% but bias −8.23%; I price+win52 15.16%
- recommendation: index_residual (+hl90) promising but research spike only; prod default stayed `log1p(price)` (infra in [[code/src/krisha/targets.py]])
- per-run reports: `A_price_report.md` … `I_price_win52_report.md`

## Gotchas
- both research folders are on SYNTHETIC data, and predate the 10.2026 bench rewrite (backtest now shares prod pipeline) → treat conclusions as provisional
- raw `*_predictions.csv` / `*_report.json` are not committed
