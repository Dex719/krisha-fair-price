---
type: file
path: scripts/loadtest.py
tags: [zone/scripts, layer/ops, layer/perf]
updated: 2026-11-03
---
# scripts/loadtest.py
purpose: load measurement of public endpoints — sequential latency + parallel ceiling; JSON output p50/p95/p99 ms, rps, status codes. Never hits `/api/predict` (it scrapes krisha.kz).
## CLI
- positional `base` URL; `--paths` (default `/api/health /api/stats /api/demo / /static/design.css`), `--count 25`, `--workers 20`, `--seconds 8.0`, `--timeout 60`
## Depends on
- httpx only (no src imports); target = [[concepts/web-api]]
## Used by
- manual. Local: `KRISHA_DB_AUTO=0 RATE_LIMIT_PER_WINDOW=100000 uvicorn krisha.api.app:app --port 7801`; prod (`https://dex719-krisha-fair-price.hf.space`, careful: rate limit per IP) — [[concepts/prod-runtime]]
## Gotchas
- prod is HF Spaces free 2 vCPU; meaningful local numbers only under `--cpus=2`
