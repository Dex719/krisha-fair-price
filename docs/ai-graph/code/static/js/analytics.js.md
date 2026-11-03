---
type: file
path: static/js/analytics.js
tags: [zone/static, layer/frontend]
updated: 2026-11-03
---
# static/js/analytics.js
purpose: GA4 counter; injected into <head> by server only when `GA4_MEASUREMENT_ID` set (`src/krisha/api/site_analytics.py`), id via `data-ga`.
## Key symbols
- `window.bagamTrack(name, params)`; auto `bot_click{place}` on any `a[href*="t.me/fairprice_kzbot"]`
- gtag loaded on idle after `load`; `allow_google_signals:false`, no ad personalization
## Data / env
- env `GA4_MEASUREMENT_ID`; host googletagmanager.com
## Gotchas
- Off inside Telegram Mini App (`tgWebApp` in URL or `TelegramWebviewProxy`) — privacy promise.
- No Yandex Metrika (removed for 1.0.0).
