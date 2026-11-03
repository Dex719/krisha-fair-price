---
type: file
path: static/js/site.js
tags: [zone/static, layer/frontend]
updated: 2026-11-03
---
# static/js/site.js
purpose: shared script for all pages (defer); exposes `window.bagam`; no animation libs (IntersectionObserver + CSS + own frames).
## Key symbols
- API doc header `static/js/site.js:1-27`: `ready(fn)`, `parseLot(v)`→`{id}|{err:empty|many|bad}`, `lotError`, `put(key,text)`, `live` Promise<{stats,health}>, `ru/fmt/pct/plural/esc`, `glide`, `reveal`, `countUp`, `markCurrent`, `tg(fn)`, `theme/setTheme`, `motion/setMotion`, `rm`, `lite`, `track`
- `getJSON(path)` `:384`; GET `/api/stats` + `/api/health` `:404`; cached in localStorage `bagam-stats`
- motion `still` `:60`, `applyMotion` `:69`, `setMotion` `:75` (`bagam-motion` overrides system)
- theme `paintTheme/setTheme` `:143-153`, event `bagam:theme`; Telegram Mini App header `tgHeader` `:176`
- mobile menu focus trap `:238-261`; `parseLot` `:586`, `fieldError` `:603`
- document events: `stats`, `health`
## Depends on
- [[concepts/web-api]]
## Used by
- [[code/static/index.html]], [[code/static/stats.html]], [[code/static/bot.html]], [[code/static/about.html]]
## Gotchas
- Motion policy: `still()` = only `lite` (weak device / saveData), NOT reduced-motion. #226 broke it, #234 restored, e2e pins it ([[history/owner-preferences]]).
- `<html data-live="off">` (404) → no API calls, `live` resolves {null,null}.
