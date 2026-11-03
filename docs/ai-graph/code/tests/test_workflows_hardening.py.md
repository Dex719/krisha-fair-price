---
type: file
path: tests/test_workflows_hardening.py
tags: [zone/tests, layer/ops]
updated: 2026-11-03
---
# tests/test_workflows_hardening.py
purpose: static + behavioural guards for GitHub Actions hardening (security audit 2026-10-06).

## Key symbols
- Finding 1: every `uses:` pinned to full commit SHA with tag comment; same tag → same SHA everywhere; Dependabot updates actions.
- Finding 2: nightly `audit.yml` (schedule+manual, not push), checks every lock file as-is, py3.11, pip-audit, Telegram on failure; CI and nightly ignore the same vulns.
- Finding 3: `run:` never interpolates `${{ inputs/github.event }}`; detector proven non-vacuous; rescrape/rent-rescrape/backfill/rent-bootstrap inputs cannot inject shell (runs real bash snippets with fake `python`).
- Finding 4: deploy-hf.yml token not in push URL; snapshot is explicit allowlist (not `git add -A`); order lfs→add→commit→push; credential helper reads token from env.

## Depends on
- [[ops/workflows/_dir]], [[ops/workflows/ci]], [[ops/workflows/rescrape]], [[ops/workflows/deploy-hf]], [[ops/workflows/audit]]

## Gotchas
- Behavioural tests skip without working bash (Windows dev).
- Adding a workflow input → pass via `env:` not inline expression, or this fails.
