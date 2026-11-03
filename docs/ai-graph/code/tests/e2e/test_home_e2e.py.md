---
type: file
path: tests/e2e/test_home_e2e.py
tags: [zone/tests, layer/e2e]
updated: 2026-11-03
---
# tests/e2e/test_home_e2e.py
purpose: largest browser e2e (~825 lines): real chromium executes home page JS against hermetic uvicorn; covers what string tests (test_home_*.py) cannot.

## Key symbols (tests)
- report rendering: fair/overpriced/good-deal(scam warning)/no-price/duplicate-repost/unknown factor hidden/factors path/HTML escaping
- errors: 5xx friendly status, removed listing detail, 422, 429 message, 45s give-up, dead market API, other city reason
- UX: client URL validation, demo button, hash deeplink, permalink, double submit, theme toggle persisted (default dark, old key migrated), no console errors, mobile no horizontal scroll, mobile menu Escape
- rent: monthly report, room share has no verdict, sale after rent restores units, rental yield shown only for sale

## Depends on
- [[code/tests/e2e/conftest.py]], [[code/static/_dir]], [[concepts/frontend]]

## Gotchas
- Selectors after redesign listed in module docstring (`form[data-check]`, `#lotUrl`, `.gobtn`, `#checkStatus`, `.sheet`, `#rFair`, `#fxList .fx`, `#rWarn`, `data-l` live numbers). Update tests together with markup.
