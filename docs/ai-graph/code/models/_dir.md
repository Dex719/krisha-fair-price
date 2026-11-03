---
type: dir
path: models/
tags: [zone/models, layer/ml]
updated: 2026-11-03
---
# models/
purpose: model artifacts and committed geo/catalogue snapshots. Path constants in [[code/src/krisha/config.py]] (`MODELS_DIR`, `RENT_MODELS_DIR`, `*_SNAPSHOT_PATH`, `METRICS_HISTORY_PATH`). See [[concepts/sale-price-model]], [[concepts/rent-model]].

## Tracked in git
| file | format | producer | consumer |
|---|---|---|---|
| `model_meta.json` | JSON: `deal, features (47), cat_features (13), metrics, ppsm_maps, city_center` | [[code/scripts/train.py]] (retrain commit) | predict/API, [[code/scripts/model_gate.py]], [[code/scripts/sync_readme_metrics.py]], [[code/scripts/compare_models.py]] |
| `rent/model_meta.json` | same + `feature_defaults`; features 67, cat 16 | `train.py --deal arenda` | rent predict, gate |
| `metrics_history.jsonl` | 1 line/retrain `{trained_at, mae, mape, r2, n_train, n_test}` | `krisha.monitoring.append_metrics_history` | gate `history_check`, [[code/scripts/notify_retrain.py]] |
| `rent/metrics_history.jsonl` | same | rent retrain | same |
| `stats.json` | market stats snapshot `{total_listings, median_price, median_ppsm, by_district, price_hist, ppsm_hist, by_rooms, trend, by_category, updated_at, source}` | [[code/src/krisha/stats.py]] (`STATS_SNAPSHOT_PATH`) | `/api/stats` fallback |
| `complexes.json` | dict ЖК name → `{developer, housing_class, completion_year, construction_status, material, max_floors, apartments_count}`; 730 entries | [[code/scripts/crawl_complexes.py]] via `complexes.snapshot_complexes` | [[code/src/krisha/complexes.py]], features |
| `osm_pois.json` | `{fetched_at 2026-06-12, bbox, pois}`; counts: metro 54, school 305, kindergarten 233, park 137, supermarket 272, bus_stop 1925, big_road 5105, industrial 2475, metro_construction 159 | [[code/scripts/fetch_osm_pois.py]] | [[code/src/krisha/geo.py]] (`load_poi_index`) |
| `osm_zones.json` | `{fetched_at 2026-07-02, bbox, districts 8, microdistricts 84, micro_centroids 129, shared_pins 118}` | [[code/scripts/fetch_osm_zones.py]] | [[code/src/krisha/zones.py]] |

## Gitignored (distributed via release `model-latest` in private repo, [[concepts/state-storage]])
- `model.cbm` (~10 MB, CatBoost point), `model_quantile.cbm` (MultiQuantile 0.1/0.9 interval), `spatial_ref.json` (H3 maps of train slice), `model_gate_samples.json` (paired per-row APE + building clusters for gate bootstrap); same under `rent/`
- packaged by [[ops/workflows/retrain]] as `models.tar.gz` / `models_rent.tar.gz` (file list = `krisha.db_release.MODEL_ARTIFACT_PATHS`), uploaded by [[code/scripts/release_upload.py]]; fetched by `python -m krisha.db_release --models` ([[code/src/krisha/db_release.py]])

## Current metrics (models/model_meta.json, trained_at 2026-10-18T10:25Z, sale)
- model: MAPE 0.07216, MdAPE 0.05131, MAE 4 278 549 ₸, R² 0.9276; 95% cluster CI MAPE [0.07036, 0.07426] over 13 223 clusters
- baseline (median ₸/м² district×rooms): MAPE 0.17291, R² 0.6914
- old model on same test: MAPE 0.07095, R² 0.9256 (new was slightly worse but passed gate)
- interval: target 0.8, `coverage_test` 0.7815, median width 23.77%, cqr_scale 0.0391, quantile best_iter 791
- n_train 80 777, n_test 19 804, n_purged 2389; test window 2026-10-06..10-18 (13 d); split "time_based (test = first_seen >= last 14d) + purge + dedup relistings"
- dedup: 132 435 → 102 970 rows (−22.25%), 13 372 fingerprint groups
- final_fit: refit on train+test, 102 970 rows, 4000 iterations (best_iterations 4000 = cap)
- representativeness worst TVD 0.083 (district); time_confounding false (worst day 2026-10-04 TVD 0.137)

## Current metrics (models/rent/model_meta.json, trained_at 2026-10-18T10:47Z, rent ₸/мес)
- model: MAPE 0.10826, MdAPE 0.08328, MAE 45 339, R² 0.8392; CI [0.10542, 0.11137], 3750 clusters
- baseline MAPE 0.20587; old model MAPE 0.11154
- interval coverage 0.7842, median width 31.35%
- n_train 43 908, n_test 5278, n_purged 418; dedup 51 595 → 49 604 (−3.86%); final_fit 49 604 rows, 1807 iterations

## Metrics history (models/metrics_history.jsonl, sale MAPE by retrain)
08-02 0.0849 (n_train 18 979) · 08-09 0.0766 · 08-16 0.0758 · 08-23 0.0729 · 08-30 0.0749 · 09-06 0.0733 · 09-13 0.0769 · 09-20 0.0775 · 09-27 0.0756 · 10-04 0.0702 · 10-18 0.0722 (n_train 80 777). No 10-11 sale row (reason not recorded in this file; the row reaches git only via the retrain commit, i.e. when the gate passed).
Rent (models/rent/metrics_history.jsonl): 10-01 0.1104 · 10-04 0.1095 · 10-11 0.1047 · 10-18 0.1083.

## Gotchas
- after refit (10.2026) the shipped model saw the test set; `metrics.model` is the pre-refit holdout estimate — compare_models skips that anchor
- test windows differ weekly → history MAPEs are not strictly comparable; gate uses same-test `old_model`
- retrain workflow commits `models/ reports/ README.md` as "model: weekly retrain <date>"
