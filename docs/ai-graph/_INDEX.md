---
type: meta
updated: 2026-11-03
---
# krisha-fair-price — AI entry point
purpose: start here. Fair price of Almaty apartments (sale + long-term rent) from krisha.kz listings; site baǵam (bagam.info) + Telegram bot; FastAPI on HF Spaces (free, 2 vCPU) behind a Cloudflare worker; data/models in private repo Dex719/krisha-db.
Rules for this vault: [[_CONVENTIONS]]. Owner rules to obey without re-asking: [[history/owner-preferences]] (local-only, gitignored — absent in a fresh clone; then ask the owner).

## Open incidents
- [[incidents/2026-11-02-prod-smoke-red]] — since 2026-11-02 krisha.kz serves SafeLine challenge (468/403) on `/a/show/<id>`; prod `/api/predict` by URL → 503; nightly detail backfill = 0. OPEN.

## Architecture (concepts)
- Runtime: [[concepts/prod-runtime]] · [[concepts/web-api]] · [[concepts/frontend]] · [[concepts/telegram-bot]]
- Data in: [[concepts/scraping-krisha]] → [[concepts/state-storage]]
- ML: [[concepts/feature-engineering]] · [[concepts/location-features]] · [[concepts/sale-price-model]] · [[concepts/rent-model]] · [[concepts/price-interval-cqr]] · [[concepts/model-evaluation]]
- Automation: [[concepts/ci-pipeline]] · [[ops/workflows/_dir]]
- Quality: [[concepts/test-suite]]

## Code map
- Python package: [[code/src/_dir]] → [[code/src/krisha/_dir]] · [[code/src/krisha/scraping/_dir]] · [[code/src/krisha/api/_dir]]
- CLI/ops scripts: [[code/scripts/_dir]]
- Tests: [[code/tests/_dir]] · [[code/tests/e2e/_dir]] · [[code/tests/fixtures/_dir]]
- Static site: [[code/static/_dir]]
- Artifacts: [[code/models/_dir]] · [[code/data/_dir]] · [[code/reports/_dir]]
- Infra: [[code/infra/_dir]] · [[code/dot-github/_dir]]
- Root files: [[code/Dockerfile]] · [[code/Makefile]] · [[code/pyproject.toml]] · [[code/requirements.lock]] · [[code/dot-env.example]] · [[code/dot-dockerignore]] · [[code/SECURITY]]

## Ops
- [[ops/deploy]] · [[ops/build]] · [[ops/env-vars]] · [[ops/infra]]

## History
- [[history/changelog-timeline]] · [[history/readme-digest]] · [[history/kiro-specs]] · [[history/owner-preferences]]

## Maintenance (for agents)
- `code/src/**` file notes are generated: `python docs/ai-graph/_tools/gen.py` (hand-written text in `_tools/content.py`, imports/line numbers from AST). Then `python docs/ai-graph/_tools/fixlinks.py` (repoints dangling links to nearest `_dir`) and `python docs/ai-graph/_tools/linkcheck.py docs/ai-graph` (must report 0 dangling).
- Other notes are hand-written; update `updated:` when touching them.
- Obsidian ignores dot-names → dotfiles use `dot-` prefix (`.github` → `code/dot-github/`).
