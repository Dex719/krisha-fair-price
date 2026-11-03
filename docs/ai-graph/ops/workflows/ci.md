---
type: workflow
path: .github/workflows/ci.yml
tags: [zone/ops, layer/ci, layer/tests]
updated: 2026-11-03
---
# .github/workflows/ci.yml ("CI")
purpose: lint + unit tests + pip-audit + hermetic browser e2e on every push to main and every PR; its green status on the SHA gates [[ops/workflows/deploy-hf]].

## Trigger
`push: branches [main]`, `pull_request` (any). No concurrency group. Default permissions.

## Job `test` (no explicit timeout)
1. Install: `pip install -r requirements-train.lock`, `pip install -e . --no-deps`, `pip install -c requirements-train.lock "pytest>=8.0" "ruff>=0.4" "pytest-playwright>=0.5"`.
2. `ruff check src tests scripts`.
3. Download model for `needs_model` tests: `python -m krisha.db_release --models --require` if `KRISHA_DB_TOKEN` present; else skip (Dependabot PRs lack secret → tests skip in conftest).
4. `pytest -q` (addopts exclude `e2e` and `live` markers, see [[code/pyproject.toml]]).
5. `pip-audit -r requirements.lock --no-deps` — blocking. Temporary ignores only `--ignore-vuln ID` with comment+removal date, mirrored in [[ops/workflows/audit]] (checked by [[code/tests/test_workflows_hardening.py]]).

## Job `e2e` (15 min)
Same install; `playwright install chromium` (no `--with-deps`: apt step hung 13+ min); model download as above; `pytest -m e2e -q` (uvicorn + Playwright, network mocked, no DB).

## Secrets
`KRISHA_DB_TOKEN` (optional).

## Health
All recent runs success, 2–3 min.

## Gotchas
- Until PR #197 (2026-09-23) CI installed fresh versions from pyproject while prod used lock (starlette 1.7 vs 1.3.1) → a prod static-file gzip bug hid for a week. Now CI uses `requirements-train.lock` (superset of runtime lock, same pinned versions). "Red locally / green in CI" → check lock versions and slim-image missing system files first.
- Model weights not in git (issue #74) → artifact consistency is tested inside [[ops/workflows/retrain]], not here.
- Audits `requirements.lock` only (what the image is built from transitively), not runner env (ubuntu preinstalled setuptools PYSEC-2026-3447 is not ours).

## Related
[[concepts/test-suite]], [[concepts/ci-pipeline]], [[ops/build]]
