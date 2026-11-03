---
type: concept
tags: [zone/static, layer/frontend]
updated: 2026-11-03
---
# frontend
purpose: baǵam web UI — static HTML + vanilla JS + one CSS ([[code/static/_dir]]), served by FastAPI; also opened as Telegram Mini App.
## Pages → endpoints
- `/` [[code/static/index.html]] → POST `/api/predict`, GET `/api/demo[?deal=arenda]`
- `/stats` [[code/static/stats.html]] → GET `/api/stats`, `/api/stats/rent`
- every page via [[code/static/js/site.js]] → GET `/api/stats`, `/api/health` (live counters, MAPE)
- `/bot` [[code/static/bot.html]], `/about` [[code/static/about.html]], `/privacy`, `/terms`, 404 — static content
## Key behaviours
- deal detected server-side (`deal: arenda` → ₸/мес); verdict + range + SHAP factors + similar + sale time + yield
- deep link `#check=<id>`; track CTA → `t.me/fairprice_kzbot?start=track_<id>`
- theme dark by default, saved in localStorage `bagam-theme`; animations stay on under reduced-motion
- GA4 optional ([[code/static/js/analytics.js]]); off inside Mini App
## Depends on
- [[concepts/web-api]], [[concepts/sale-price-model]], [[concepts/rent-model]], [[concepts/telegram-bot]], [[concepts/prod-runtime]]
## Gotchas
- E2E (pytest-playwright `tests/e2e/`) pins locators and motion — [[concepts/test-suite]].
- Owner taste in [[history/owner-preferences]]: no big redesign; rejected: city photo, official tone, magnetic buttons, CSS checkmarks.
