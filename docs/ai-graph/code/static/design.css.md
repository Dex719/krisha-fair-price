---
type: file
path: static/design.css
tags: [zone/static, layer/frontend]
updated: 2026-11-03
---
# static/design.css
purpose: shared design system (redesign 2026-10); pages hold only local additions.
## Key symbols
- tokens on `:root` (dark) and `html[data-theme=light]` (warm paper): `--lime` brand/cheaper, `--vio` only «дороже», `--bad` only risk/errors; radii `--r1/--r2/--r3/--rpill`; text scale `--fs-*`; chart `--c-lo/--c-hi/--c-mid`
- fonts: Unbounded only for display (h1, verdict, big numbers); Onest 600 headings; tabular-nums; metric-matched fallbacks `OnestFB`, `UnboundedFB`
- `[hidden]{display:none!important}` guard
## Gotchas
- Design thesis comment `static/design.css:3-15` — read before visual edits. No big redesign ([[history/owner-preferences]]).
