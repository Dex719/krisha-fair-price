---
type: workflow
path: .github/workflows/deploy-hf.yml
tags: [zone/ops, layer/ci, layer/deploy]
updated: 2026-11-03
---
# .github/workflows/deploy-hf.yml ("Deploy to HF Space")
purpose: on push to main, wait for green CI on the same SHA, then force-push a one-commit snapshot (explicit file list) to the PUBLIC HF Space git repo `huggingface.co/spaces/Dex719/krisha-fair-price`, which triggers HF Docker rebuild.

## Trigger
- `push: branches [main]`, `workflow_dispatch` (skips CI gate deliberately).
- `concurrency: hf-deploy`, `cancel-in-progress: true` (bursts of pushes → only latest deploys; cancelled runs are normal).
- `permissions: contents: read, actions: read`.

## Jobs
- `gate` (35 min): on push, polls `gh api repos/$REPO/actions/workflows/ci.yml/runs?head_sha=$GITHUB_SHA` (event==push) every 20 s × 100 (~33 min). success → exit 0; other conclusion or timeout → fail (no deploy). Uses `GITHUB_TOKEN`.
- `deploy` (needs gate):
  1. Prepend HF Space front-matter to README (`sdk: docker`, `app_port: 7860`, title Krisha Fair Price, pinned).
  2. `echo $GITHUB_SHA > data/build_revision.txt` → served as `/api/health.revision` ([[code/src/krisha/api/app.py]] `_build_revision`).
  3. `rm -rf .git; git init -b main; git lfs track "data/*.db" "models/*.cbm" "*.png" "*.jpg" "*.ico"`.
  4. `git add Dockerfile README.md pyproject.toml requirements-runtime.lock .dockerignore .gitattributes src models data static` + `git add -f data/build_revision.txt`.
  5. Commit `deploy <sha>`; `git push --force https://huggingface.co/spaces/Dex719/krisha-fair-price main` with HF_TOKEN supplied via inline credential helper (token never in URL/argv).

## Secrets
`HF_TOKEN`, `GITHUB_TOKEN`.

## Downstream
Completion triggers [[ops/workflows/smoke]] (`workflow_run`), which waits until `/api/health.revision == head_sha`.

## Health
Deploys 3–4 min after gate; last pushes 2026-10-24 (success; several `cancelled` by concurrency).

## Gotchas
- Deploy used to race CI (both on same push) → red commits reached prod. `needs:` cannot cross workflows; `workflow_run` would change head_sha semantics → API polling gate.
- Security audit 2026-10-06: Space is a PUBLIC mirror; formerly `git add -A` leaked tests/, scripts/, infra/, reports/, .kiro/. Now explicit list = Dockerfile COPY set; [[code/tests/test_workflows_hardening.py]] cross-checks list vs Dockerfile. New top-level dir reaches Space only if added here; nonexistent path = fatal.
- DB and model weights are NOT in the snapshot (gitignored); container downloads them at boot from krisha-db ([[concepts/state-storage]]).
- HF assigns its own commit SHA; never compare HF API sha with GitHub sha — use `/api/health.revision`.

## Related
[[ops/deploy]], [[ops/build]], [[concepts/prod-runtime]], [[concepts/ci-pipeline]]
