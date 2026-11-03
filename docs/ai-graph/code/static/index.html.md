---
type: file
path: static/index.html
tags: [zone/static, layer/frontend]
updated: 2026-11-03
---
# static/index.html
purpose: main page `/` — paste krisha.kz link → verdict Выгодно / В рынке / Дорого, fair price, range, factors, similar lots, yield.
## Key symbols
- inline IIFE, `API='/api/predict'` `static/index.html:263`; `RX=/krisha\.kz\/a\/show\/(\d+)/`
- POST `/api/predict` body `{url:'https://krisha.kz/a/show/'+id}` `static/index.html:906`; client timeout `LIMIT=45000` ms
- `render(r)` `:613` — `RENT = r.deal==='arenda'`; `r.room_share` (подселение) → no verdict; `VERD.NO_PRICE`
- `renderWarn` `:375` (suspiciously cheap etc.), `renderHist` `:432`, `renderSimilar` `:464`, `renderYield` `:490`, `renderFactors` `:548` (SHAP, `factors_base`/`factors_other`), `drawTicks` `:595` (scale in millions)
- `trackHref` `:520` → `t.me/fairprice_kzbot?start=track_<id>`
- `showError` `:740`, `httpError` `:772` — kinds input/timeout/network/server
- deep link `#check=<id>`: push/replaceState `:918`, auto-run on load `:1052`
- demo: `[data-demo]` → GET `/api/demo`, `[data-demo-rent]` → `/api/demo?deal=arenda` `:999`
- GA events via `bagam.track`: `check_ok{deal,verdict,source}`, `check_error{kind}`, `check_bad_link`, `demo`
## Depends on
- [[code/static/js/site.js]] (`bagam.parseLot`, `lotError`, `ready`, `reveal`), [[code/static/design.css]], [[concepts/web-api]], [[concepts/sale-price-model]], [[concepts/rent-model]], [[concepts/telegram-bot]]
## Gotchas
- Inline head script sets theme (`bagam-theme`, legacy `kfp-theme`) and motion (`bagam-motion`) before first paint.
- Any 502 (Cloudflare/HF) must allow retry, not «try another lot» (audit 2026-10-24 item 2; #240–#244).
- Demo counter animation used to overwrite a real analysis — fixed pre-1.0.0.
- Only Almaty lots accepted (1.0.0).
