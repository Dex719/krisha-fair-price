---
type: file
path: static/bot.html
tags: [zone/static, layer/frontend]
updated: 2026-11-03
---
# static/bot.html
purpose: `/bot` landing for @fairprice_kzbot: commands `/track`, `/alerts_on`, `/untrack [all]`, `/alerts_off`; FAQ (free, rent support, stored data, accuracy).
## Key symbols
- FAQ accordion `.qa button.q[aria-expanded]`; live MAPE in `[data-l=mape]`, `[data-l=rmape]` filled by site.js from `/api/health`
## Depends on
- [[code/static/js/site.js]], [[concepts/telegram-bot]]
## Gotchas
- Public claims: pasted lot text goes to Gemini; chat id stored only with /track or /alerts_on; subscriptions file encrypted; hashed chat id for user count.
