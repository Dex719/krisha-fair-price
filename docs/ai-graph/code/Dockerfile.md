---
type: file
path: Dockerfile
tags: [zone/root, layer/build]
updated: 2026-11-03
---
# Dockerfile
purpose: prod image for HF Spaces (any Docker host): slim Python 3.11, runtime lock, editable install, uvicorn 2 workers on :7860.

## Key lines
- `:5` `FROM python:3.11.15-slim@sha256:baf89808…` (pin, issue #119).
- `:9` apt `curl` only (for HEALTHCHECK).
- `:11` `useradd -u 1000 app`; `:33` `USER app`.
- `:14,22` copy + `pip install --no-cache-dir --no-deps -r requirements-runtime.lock` (cached layer).
- `:23,27` `COPY src`; `pip install -e . --no-deps` (ROOT_DIR=/app).
- `:29-31` `COPY models data static`.
- `:39-43` ENV PYTHONUNBUFFERED, OMP/OPENBLAS/MKL/NUMEXPR_NUM_THREADS=1 (was OMP=2; 2 workers × 2 threads oversubscribed 2 vCPU).
- `:53-54` HEALTHCHECK interval 60 s / timeout 35 s / start 60 s / retries 3, `curl --max-time 30 /api/health`.
- `:63-64` `ENV WEB_CONCURRENCY=2`; `CMD sh -c "exec uvicorn krisha.api.app:app --host 0.0.0.0 --port 7860 --workers ${WEB_CONCURRENCY:-2}"`.

## Depends on
[[code/requirements.lock]] (runtime variant), [[code/pyproject.toml]], [[code/dot-dockerignore]].

## Used by
[[ops/workflows/deploy-hf]] (snapshot file list must equal the COPY set — [[code/tests/test_workflows_hardening.py]]). Overview [[ops/build]].

## Gotchas
- `/app` owned by root → runtime temp files only under `models/`/`data/`.
- slim lacks `/etc/mime.types` (past webp/woff2 bug, PR #196).
- HEALTHCHECK timeout was 5 s → healthy containers declared dead when api.telegram.org unreachable (issue #119).
