---
type: file
path: src/krisha/api/live_pages.py
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# src/krisha/api/live_pages.py
purpose: Server-side fill of [data-l=…] live numbers, district bars, FAQ/JSON-LD, rent variant of stats page, <noscript> market summary and /llms.txt — formats identical to static site.js.
concepts: [[concepts/web-api]], [[concepts/frontend]]
## Key symbols
- `live_values` — texts for [data-l] keys from stats+health `src/krisha/api/live_pages.py:99`
- `fill_live` — substitute values in HTML `src/krisha/api/live_pages.py:154`
- `district_bars` — homepage bars markup like draw() in index.html `src/krisha/api/live_pages.py:188`
- `faq_json_ld` — FAQPage from visible .qa blocks `src/krisha/api/live_pages.py:265`
- `rent_variant` — stats.html for /stats?mode=rent with own title/canonical `src/krisha/api/live_pages.py:305`
- `llms_txt` — /llms.txt text `src/krisha/api/live_pages.py:423`
## Used by
- [[code/src/krisha/api/app.py]]
- tests: [[code/tests/_dir|tests/test_seo_live_pages.py]]
## Gotchas
- Formats must match site.js char-for-char (e.g. `_js_round` = JS Math.round) or numbers flicker after API response.
