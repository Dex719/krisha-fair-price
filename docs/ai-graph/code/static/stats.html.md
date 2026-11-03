---
type: file
path: static/stats.html
tags: [zone/static, layer/frontend]
updated: 2026-11-03
---
# static/stats.html
purpose: `/stats` «Рынок Алматы» — sale prices / rent by district and rooms, weekly trend, yield; sale/rent switch.
## Key symbols
- pre-paint mode `html[data-mode=sale|rent]` from `?mode=rent` `static/stats.html:20`; per-mode text via `[data-show]`
- GET `/api/stats` (sale) or `/api/stats/rent` `:439`, `:894`; same-origin, AbortController
- sections `#trend` (←/→ = week nav), `#districts`, `#prices`, `#rooms`, `#yield`, `#check` (`form[data-jump]` → main page check)
- GA event `market_mode` on switch
## Depends on
- [[code/static/js/site.js]], [[concepts/web-api]], [[concepts/rent-model]]
## Gotchas
- SEO audit 05.10: rent canonical glued to /stats; robots `Disallow: /api/`.
