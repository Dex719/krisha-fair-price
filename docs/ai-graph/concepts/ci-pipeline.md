---
type: concept
tags: [zone/ops, layer/ci]
updated: 2026-11-03
---
# CI pipeline (GitHub Actions schedule and data flow)
purpose: one-page map of all workflows, when they run, what they touch, and how data flows scrape → private repo → retrain → prod.

## Schedule map (cron in UTC; Almaty = UTC+5)
| time UTC (cron) | days | workflow | touches |
|---|---|---|---|
| 00:30, 06:30, 12:30, 18:30 | daily | [[ops/workflows/space-keepalive]] | GET prod /api/health; Telegram on state change; actions/cache |
| 01:00 | daily | [[ops/workflows/rescrape]] (sale) | krisha.kz → krisha-db `db-latest` (krisha.db.gz, sweep_state_prodazha.json), `snapshot-<date>`, state JSON; HF restart; Telegram channel/subscribers/admin |
| 02:00 | Sunday | [[ops/workflows/retrain]] (sale, then rent) | reads db-latest + model-latest; writes `model-latest`, `model-<date>`; commits meta/README to main (→ CI + deploy); HF restart |
| 03:17, 15:17 | daily | [[ops/workflows/smoke]] | prod pages/API/demo predict; Telegram on failure |
| 04:23 | daily | [[ops/workflows/audit]] | pip-audit of 3 locks; Telegram on failure |
| 10:00 | Wednesday | [[ops/workflows/restore-drill]] | read-only: db-latest (both DBs) + model-latest, boot app in runner |
| 15:00 | daily | [[ops/workflows/rescrape-rent]] | krisha.kz rent → `db-latest` (krisha_rent.db.gz, sweep_state_arenda.json), snapshot; HF restart; rent /track alerts; admin rent report |
| push main / PR | event | [[ops/workflows/ci]] | lint, pytest, pip-audit, e2e |
| push main | event | [[ops/workflows/deploy-hf]] | waits CI green → force-push snapshot to HF Space |
| after deploy success | workflow_run | [[ops/workflows/smoke]] | waits revision, smoke |
| manual | dispatch | [[ops/workflows/backfill-gap-cohort]] | one-off DB repair in db-latest |
| Monday | weekly | Dependabot ([[code/dot-github/dependabot.yml]]) | PRs for pip (pyproject) + github-actions |

Observed reality: GitHub starts scheduled runs late — rescrape 4.8–6.7 h after cron (05:50–07:44 UTC), rent 3–8 h, smoke 6–8 h. Treat cron times as "not earlier than". Retrain additionally waits in queue for the Sunday rescrape.

## Concurrency groups (serialize writers of the same release asset)
- `krisha-db-writes`: rescrape, retrain (both jobs), backfill (deal=prodazha).
- `krisha-rent-db-writes`: rescrape-rent, backfill (deal=arenda).
- `hf-deploy` (cancel-in-progress), `prod-smoke`, `dependency-audit`, `restore-drill`.
- Never set `cancel-in-progress: true` on DB writers — a cancelled pass loses scraped data.

## Data flow
```
krisha.kz ──scripts/rescrape.py──▶ data/krisha.db (runner)
   ▲ download (db_release --require)          │ VACUUM+gzip+sha256
   │                                           ▼ release_upload.py (atomic .uploading rename)
Dex719/krisha-db release db-latest ◀───────────┘   + snapshot-YYYY-MM-DD (publish_snapshot.py)
   │                     │
   │ weekly retrain       │ HF restart → Space boot: ensure_db / ensure_rent_db
   ▼                     ▼
scripts/train.py → model_gate.py ─pass─▶ release model-latest (+model-YYYY-MM-DD) → HF restart → ensure_models
                                  └──▶ commit models/*meta*, reports/, README.md → main → CI → deploy-hf → smoke
bot state JSON (subscriptions, tracked, usage, prediction_log, channel_posted, alerted_*) ⇄ krisha-db contents data/*.json
   (Space at runtime + Alerts steps in rescrape workflows)
```
Details: [[concepts/state-storage]], [[concepts/prod-runtime]], [[concepts/scraping-krisha]], [[concepts/sale-price-model]], [[concepts/rent-model]].

## Shared conventions across workflows
- Python 3.11, SHA-pinned actions, `pip install -r <lock>` + `pip install -e . --no-deps`.
- `KRISHA_DATA_REPO: Dex719/krisha-db` in workflow env; GITHUB_TOKEN cannot reach private repo → all release ops via `KRISHA_DB_TOKEN` (passed as `GH_TOKEN` for gh CLI).
- dispatch inputs only through `env:` (shell-injection rule; [[code/tests/test_workflows_hardening.py]], [[code/tests/_dir|tests/test_rescrape_workflow.py]], [[code/tests/_dir|tests/test_retrain_workflow.py]]).
- Every secondary step: own `timeout-minutes` + `continue-on-error`; critical path (DB upload) first.
- Telegram failure notifications skip silently when secrets missing (forks).
- Owner rule (memory): after merge do not wait on CI/deploy and do not leave background pollers.

## Health snapshot 2026-11-03
rescrape 10/10 green (187–303 min, near 300 budget); rent 10/10 green; retrain gate rejected sale model on 10-11 and 10-25 (expected); smoke red since 2026-11-02 11:06 (demo predict 503 from krisha source); audit, keepalive green; restore-drill 1/1 green.
