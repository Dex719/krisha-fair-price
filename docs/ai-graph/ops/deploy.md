---
type: ops
tags: [zone/ops, layer/deploy]
updated: 2026-11-03
---
# Deploy
purpose: how code, data and models reach production and how to verify.

## Code deploy path
1. Push/merge to `main` of public Dex719/krisha-fair-price.
2. [[ops/workflows/ci]] and [[ops/workflows/deploy-hf]] start in parallel; deploy `gate` job polls CI for the same SHA (≤33 min), aborts unless success.
3. deploy job force-pushes one-commit snapshot (Dockerfile, README with HF front-matter, pyproject.toml, requirements-runtime.lock, .dockerignore, .gitattributes, src, models, data incl. `data/build_revision.txt`, static) to `https://huggingface.co/spaces/Dex719/krisha-fair-price` (git LFS for *.db, *.cbm, images).
4. HF rebuilds Docker image ([[ops/build]]) and replaces container — no blue/green, brief downtime.
5. [[ops/workflows/smoke]] (workflow_run) waits for `/api/health.revision == SHA` (≤15 min), then runs [[code/scripts/smoke_prod.py]].
Manual: `workflow_dispatch` on deploy-hf skips the CI gate.

## Data / model refresh (no rebuild)
- Container boot ([[code/src/krisha/api/app.py]] `_startup` → `_prepare_data` under `fcntl.flock(data/.startup.lock)` so 2 workers download once): `subscriptions.pull_state()` → `db_release.ensure_db()`, `ensure_models()`, `ensure_rent_db()`, `ensure_rent_models()` → `init_db` migrations → cache warmup.
- So fresh data/models = just restart Space: `POST https://huggingface.co/api/spaces/Dex719/krisha-fair-price/restart` (Bearer HF_TOKEN). Done by [[ops/workflows/rescrape]], [[ops/workflows/rescrape-rent]], [[ops/workflows/retrain]], and manual [[ops/workflows/space-keepalive]] `restart=true`.

## Verify prod
- `curl https://dex719-krisha-fair-price.hf.space/api/health` → `revision`, `model_loaded`, checks. Same via `https://bagam.info/api/health` (Cloudflare worker, [[ops/infra]]).
- Single requests only; HF bans IPs for load testing. Comparable perf numbers only via local `docker run --cpus=2`.
- HF container logs visible only to logged-in owner (API → 401). Logs and in-memory `/api/metrics` reset on every restart (≥1/day).

## Domains
- Origin: `dex719-krisha-fair-price.hf.space` (HF Spaces free cpu-basic: 2 vCPU, 16 GB).
- Public: `bagam.info` (+ `www` → 301) via Cloudflare Worker `bagam-proxy` = [[code/infra/domain-worker.js]]. Registrar Northwest; NS Cloudflare. Live since 2026-10-03.
- Bot webhook stays on hf.space address (not via Cloudflare).

## Rollback
No automated rollback. Options: revert commit on main (re-deploys); models — re-upload previous `model-YYYY-MM-DD` archive into `model-latest` with [[code/scripts/release_upload.py]] and restart; DB — `snapshot-YYYY-MM-DD` release ([[concepts/state-storage]]).

## Gotchas
- Never put tokens in push URL — credential helper pattern in deploy-hf.
- Space is a PUBLIC repo: anything added to the snapshot list is public.
- Owner-only manual steps: re-deploy Cloudflare workers after infra/ changes (UI only, API blocked by WAF); secret and Cloudflare settings are the owner's. Their current state is not recorded here — ask.
- Release 1.0.0 tagged at a29107b.
