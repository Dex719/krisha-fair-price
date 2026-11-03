---
type: meta
updated: 2026-11-03
---
# Vault conventions (read first)

Audience: AI agents, not humans. Dense facts, no prose filler. English; keep Russian domain terms (ЖК, тенге) when they are identifiers or UI text.
Vault root = `docs/ai-graph/` (Obsidian). Every link is a vault-root path wikilink: `[[code/src/krisha/config.py]]`, never a bare name.

## Folders and note names
- `code/<repo path>.md` — one note per meaningful source file, mirroring repo path. Link: `[[code/src/krisha/api/app.py]]`. Dot-names are invisible to Obsidian → prefix `dot-` (`.github/` → `code/dot-github/`, `.env.example` → `code/dot-env.example.md`). Directory summary note: `code/<dir>/_dir.md` → `[[code/src/_dir]]`.
- `ops/workflows/<yml name without .yml>.md` — one per GitHub workflow → `[[ops/workflows/smoke]]`.
- `ops/<topic>.md` — deploy, infra, env vars, secrets (names only, never values).
- `concepts/<kebab-case>.md` — cross-cutting ideas (pipeline stages, models, data stores, external services).
- `incidents/<yyyy-mm-dd>-<slug>.md` — failures and their root causes.
- `history/` — decisions and PR timeline distilled from CHANGELOG/README/memory.
- `_INDEX.md` — entry point (built last).

## Note template
```
---
type: file | dir | workflow | concept | ops | incident | history
path: src/foo.py            # file/dir/workflow notes only
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# <title = repo path or concept name>
purpose: <one line>
## Key symbols        # name(signature) — what, `path:line`
## Depends on         # wikilinks
## Used by            # wikilinks
## Data / env         # files read/written, env vars, external hosts
## Gotchas            # invariants, past bugs with PR numbers, traps
```
Omit empty sections. Prefer bullets and `path:line`. No secret values.

## Shared concept names (link freely; owner zone creates the note)
- [[concepts/prod-runtime]] (ops) — HF Space + bagam.info + Cloudflare worker
- [[concepts/scraping-krisha]] (src) — scraping pipeline, SafeLine anti-bot
- [[concepts/sale-price-model]] (src) — sale price model, training, inference
- [[concepts/rent-model]] (src) — rent model and yield
- [[concepts/state-storage]] (ops) — private Dex719/krisha-db repo, snapshots, DB files
- [[concepts/web-api]] (src) — HTTP app, endpoints
- [[concepts/frontend]] (static) — static site/UI
- [[concepts/telegram-bot]] (src) — bot, if present
- [[concepts/test-suite]] (tests)
- [[concepts/ci-pipeline]] (ops)
