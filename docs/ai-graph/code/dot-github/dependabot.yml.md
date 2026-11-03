---
type: file
path: .github/dependabot.yml
tags: [zone/ops, layer/ci]
updated: 2026-11-03
---
# .github/dependabot.yml
purpose: weekly (Monday) Dependabot PRs for `pip` (directory `/`, open-PR limit 5) and `github-actions`.

## Gotchas
- pip ecosystem reads only pyproject.toml / requirements*.txt; it does NOT see `requirements*.lock` (audit 24.10: 0 alerts while pillow had 38 CVEs). Lock security = [[ops/workflows/audit]]; bump via `make lock UV_FLAGS="--upgrade-package X"`.
- Dependabot PRs run CI without `KRISHA_DB_TOKEN` unless set in Dependabot secrets → `needs_model` tests skip.
- Actions bumps must keep SHA pins (repo setting `sha_pinning_required=true`).
Related: [[ops/build]], [[concepts/ci-pipeline]].
