---
type: dir
path: static/
tags: [zone/static, layer/frontend]
updated: 2026-11-03
---
# static/
purpose: vanilla-JS static site baǵam (bagam.info), served by FastAPI ([[code/src/krisha/api/app.py]]); no frameworks, no build step.
## Files
- [[code/static/index.html]] — `/` main: link check → verdict (POST /api/predict), demo
- [[code/static/stats.html]] — `/stats` market dashboard, sale/rent (`?mode=rent`)
- [[code/static/bot.html]] — `/bot` Telegram bot landing + FAQ
- [[code/static/about.html]] — `/about` methodology + FAQ
- `privacy.html`, `terms.html` — legal text; privacy keeps TODO(владелец) about operator name: do not touch, do not remind ([[history/owner-preferences]])
- `404.html` — `<html data-live="off">`: no API calls
- [[code/static/js/site.js]] — shared runtime `window.bagam`
- [[code/static/js/analytics.js]] — GA4, injected only when env set
- [[code/static/design.css]] — shared design system
- `fonts/` (Onest, Unbounded subsets; `unb-gacute.woff2` = glyph ǵ for logo), `img/` (skyline.svg, city-1400.webp, og.jpg), favicons, `site.webmanifest`
## Depends on
- [[concepts/web-api]], [[concepts/frontend]]
## Gotchas
- Pages served at clean paths (`/about`); `/rent` → 301 `/stats?mode=rent` (1.0.0).
- Statics precompressed in memory and URL-versioned by `src/krisha/api/static_cache.py` (`/static/<file>` refs rewritten at build).
- Server injects live numbers, JSON-LD, FAQPage and GA tag into HTML at startup.
- Missing `/etc/mime.types` in slim image once served webp/woff2 as octet-stream (test_static_precompress, PR #197).
