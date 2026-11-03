---
type: concept
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# Price interval (MultiQuantile + CQR) and verdict
purpose: the «обычный диапазон» shown with every estimate, which also drives the verdict and the «подозрительно дёшево» badge.

## Training ([[code/src/krisha/train.py]])
1. One CatBoost `MultiQuantile:alpha=0.10,0.90` model (issue #132; replaced separate model_lo/model_hi), ≤800 iterations, ES 100 rounds.
2. Held-out calib part of train (with its own ppsm maps / spatial ref — issue #105 fixed calib prices leaking into their own features) → width-normalized conformity scores E = max(lo−y, y−hi)/(hi−lo) → `_cqr_scale`: quantile at level ⌈(n+1)·c⌉/n, c = 0.80 + `CQR_COVERAGE_MARGIN` 0.01, n = number of buildings; clipped to [0, 10].
3. Interval = [lo − scale·w, hi + scale·w], w = max(hi−lo, 0.05) in log space → `expm1` → `finalize_interval` ([[code/src/krisha/interval.py]]: swap, contain point).
4. Coverage and median relative width on test go to meta `metrics.interval` (gate thresholds in [[code/scripts/model_gate.py]]); final quantile model refit on all data with ES iterations, scale carried over.

## Inference ([[code/src/krisha/predict.py]])
- `load_interval_models` (lru_cache): model_quantile.cbm, legacy pair, or None.
- `_apply_cqr`: new meta `cqr_scale`; old meta `cqr_offset_log` (fixed shift).
- `_verdict_interval`: GOOD_DEAL < low, OVERPRICED > high, else FAIR; no interval → ±10% (`VERDICT_THRESHOLD`).
- Scam badge ([[code/src/krisha/scam.py]]): price < low → medium; ≥15% below low AND listing ≤7 days old → high.
- Alerts send only GOOD_DEAL; suspicious ones sorted last ([[code/src/krisha/alerts.py]]).

## Gotchas
- train and predict must use the same finalize/CQR code (issue #105) or the gate measures an interval users never see.
- Observed coverage was ~78.9% at 80% target before the +1 pp margin (comment in train.py).
