---
type: dir
path: .github/workflows
tags: [zone/ops]
updated: 2026-11-03
---
# .github/workflows
purpose: index of all GitHub Actions workflows; schedule table and data flow live in [[concepts/ci-pipeline]].
## Workflows
- [[ops/workflows/ci]] — lint + tests + pip-audit + e2e on push/PR; gates deploy
- [[ops/workflows/deploy-hf]] — push snapshot to HF Space after green CI
- [[ops/workflows/smoke]] — live prod check after deploy and twice daily
- [[ops/workflows/rescrape]] — nightly sale scrape → `db-latest` → restart Space → alerts
- [[ops/workflows/rescrape-rent]] — nightly rent scrape
- [[ops/workflows/retrain]] — weekly sale + rent retrain with metric gate
- [[ops/workflows/space-keepalive]] — 6-hourly ping + content health
- [[ops/workflows/restore-drill]] — weekly restore proof of `db-latest`
- [[ops/workflows/audit]] — daily pip-audit of lock files
- [[ops/workflows/backfill-gap-cohort]] — one-off manual backfill
## Gotchas
- Cron times are "not earlier than": scheduled runs start hours late (see [[concepts/ci-pipeline]]).
- Tests that pin workflow structure: [[code/tests/test_workflows_hardening.py]].
- Open incident: [[incidents/2026-11-02-prod-smoke-red]].
