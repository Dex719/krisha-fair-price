---
type: workflow
path: .github/workflows/audit.yml
tags: [zone/ops, layer/ci, layer/security]
updated: 2026-11-03
---
# .github/workflows/audit.yml ("Dependency audit")
purpose: daily `pip-audit` of all three lock files, independent of commits (new CVEs on pinned versions appear without pushes; Dependabot cannot read `requirements*.lock`).

## Trigger
`cron "23 4 * * *"` = 04:23 UTC = 09:23 Almaty; `workflow_dispatch`. `concurrency: dependency-audit`. `permissions: contents: read`. 15 min.

## Steps
1. `pip install -q pip-audit` (no project deps).
2. Loop `requirements.lock`, `requirements-runtime.lock`, `requirements-train.lock`: `pip-audit -r $lock --no-deps || status=1` (all three reported, then exit status).
3. On failure → Telegram sendMessage "baǵam dependency audit failed" to `TG_ADMIN_CHAT_ID` (skip if secrets missing).

## Secrets
`TELEGRAM_BOT_TOKEN`, `TG_ADMIN_CHAT_ID`.

## Health
Daily success 2026-10-25..11-03 (observed start 09:14–12:39 UTC).

## Gotchas
- Added by security audit 2026-10-06 (PR #236). Ignore list (`--ignore-vuln ID`) must match [[ops/workflows/ci]]; enforced by [[code/tests/test_workflows_hardening.py]].
- Audit 24.10: Dependabot showed 0 alerts while pillow had 38 CVEs — locks are invisible to it. Fix path: `make lock UV_FLAGS="--upgrade-package <pkg>"` ([[ops/build]]).

## Related
[[code/dot-github/dependabot.yml]], [[concepts/ci-pipeline]]
