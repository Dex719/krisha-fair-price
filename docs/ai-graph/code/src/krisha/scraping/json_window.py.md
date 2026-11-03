---
type: file
path: src/krisha/scraping/json_window.py
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# src/krisha/scraping/json_window.py
purpose: Extract inline `window.<name> = {...};` JSON from krisha HTML robustly (issue #100).
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `extract_window_json` — find `window.<var> =` start, then `json.JSONDecoder.raw_decode` to find the real end `src/krisha/scraping/json_window.py:32`
## Used by
- [[code/src/krisha/scraping/complex_parser.py]]
- [[code/src/krisha/scraping/detail_parser.py]]
- tests: [[code/tests/_dir|tests/test_json_window.py]]
## Gotchas
- Old non-greedy regex `(\{.*?\});` cut at the first `};` inside strings/nested objects and silently dropped listings; do not reintroduce a regex end-match.
