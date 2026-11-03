---
type: workflow
path: .github/workflows/retrain.yml
tags: [zone/ops, layer/ci, layer/ml]
updated: 2026-11-03
---
# .github/workflows/retrain.yml ("Weekly retrain")
purpose: weekly CatBoost retrain of sale model and rent model with a metric gate vs the current prod model; publish weights to private `model-latest`; commit meta/README to main; restart Space.

## Trigger
- `cron "0 2 * * 0"` = Sunday 02:00 UTC = 07:00 Almaty. Queues behind [[ops/workflows/rescrape]] (same `concurrency: krisha-db-writes`, no cancel) → effectively runs after the Sunday sale pass finishes.
- `workflow_dispatch` input `deal`: choice both|prodazha|arenda (default both). On schedule inputs are empty → both jobs run.
- `permissions: contents: write` (commits to main). `KRISHA_DATA_REPO=Dex719/krisha-db`.

## Job `retrain` (sale; `if: inputs.deal != 'arenda'`, 60 min)
1. Install `requirements-train.lock` + `-e . --no-deps`.
2. `python -m krisha.db_release --require` (DB) and `--models --require` (current weights). [[code/src/krisha/db_release.py]]
3. Snapshot old: `models/model_meta.json`→`/tmp/old_meta.json`, `model.cbm`→`/tmp/old_model.cbm`, `model_quantile.cbm`→`/tmp/old_model_quantile.cbm` (train.py looks for `<name>_quantile.cbm` beside old point model to gate interval width).
4. `Train` — `python scripts/train.py --compare-old /tmp/old_model.cbm --compare-old-meta /tmp/old_meta.json`. [[code/scripts/train.py]] → [[code/src/krisha/train.py]]
5. `Metric gate` (id gate) — `python scripts/model_gate.py /tmp/old_meta.json models/model_meta.json --summary $GITHUB_STEP_SUMMARY`; non-zero = new model worse → job stops (no publish). [[code/scripts/model_gate.py]]
6. Telegram report (always if gate ran) — `scripts/notify_retrain.py ... --gate <outcome>`. [[code/scripts/notify_retrain.py]]
7. `Model artifact tests` — `pytest -q tests/test_model_artifacts.py` on fresh files. [[code/tests/test_model_artifacts.py]]
8. `Publish models release` — tar `MODEL_ARTIFACT_PATHS` (model.cbm, model_quantile.cbm, model_meta.json, spatial_ref.json, complexes.json) → `models.tar.gz`+.sha256 → [[code/scripts/release_upload.py]] to `model-latest` (create if missing) and dated `model-YYYY-MM-DD`.
9. `Sync README metrics` — [[code/scripts/sync_readme_metrics.py]] (same commit as meta; [[code/tests/_dir|tests/test_readme.py]] runs `--check`).
10. `Commit model metadata` — `git add models/ reports/ README.md`, commit `model: weekly retrain <date>` as github-actions[bot], `pull --rebase`, push main. Weights/spatial_ref/gate samples are gitignored.
11. `Restart Space` — HF restart API; Space downloads `model-latest` on boot (`ensure_models`). Not continue-on-error here.

## Job `retrain-rent` (`needs: retrain`, `if: always() && inputs.deal != 'prodazha'`, 60 min)
- Download rent DB `--rent --require`; list `model-latest` assets via `gh release view`; if `models_rent.tar.gz` present → `--rent --models --require`; `has_old` output.
- Train `scripts/train.py --deal arenda [--compare-old ...]`.
- Gate: with old → `model_gate.py ... --samples models/rent/model_gate_samples.json`; first model → inline sanity: MAPE < 20% and < baseline.
- Telegram report only if has_old. `pytest -q tests/test_rent_model_artifacts.py`. [[code/tests/_dir|tests/test_rent_model_artifacts.py]]
- Publish `models_rent.tar.gz` (RENT_MODEL_ARTIFACT_PATHS) to `model-latest` + `model-<date>`; commit `models/rent/` (`model: weekly retrain аренды <date>`); restart Space.

## Secrets
`KRISHA_DB_TOKEN` (+ `GH_TOKEN`), `TELEGRAM_BOT_TOKEN`, `TG_ADMIN_CHAT_ID`, `HF_TOKEN`; `GITHUB_TOKEN` implicit for push.

## Writes to
krisha-db releases `model-latest`, `model-YYYY-MM-DD`; public repo main (`models/model_meta.json`, `models/metrics_history.jsonl`, `models/stats.json`, `reports/`, `README.md`, `models/rent/*`). Push to main triggers [[ops/workflows/ci]] + [[ops/workflows/deploy-hf]] (full Space rebuild).

## Health
Wall time 130–291 min (mostly queue wait behind rescrape). Failures 2026-10-11 and 2026-10-25 = `Metric gate` rejected new sale model ("новая модель хуже — оставляем старую") — expected behavior, not infra breakage; rent job still succeeded.

## Gotchas
- Publish happens BEFORE commit: if publish fails, README/meta in git don't get ahead of the model prod actually serves.
- Audit 24.10: `upload --clobber || release create` once left `model-latest` without archive → prod lived a week without model after restart. Now release_upload.py.
- Rent first-model detection used to treat download failure as "no old model" → ungated model to prod; now asset list via gh (gh error fails step) + `--require`.
- Sale and rent jobs sequential because both push to main.

## Related
[[concepts/sale-price-model]], [[concepts/rent-model]], [[concepts/ci-pipeline]], [[concepts/state-storage]]
