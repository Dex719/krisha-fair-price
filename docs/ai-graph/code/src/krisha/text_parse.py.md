---
type: file
path: src/krisha/text_parse.py
tags: [zone/src, layer/bot, layer/ml]
updated: 2026-11-03
---
# src/krisha/text_parse.py
purpose: Estimate from pasted listing text: Gemini extracts params with strict JSON schema → listing dict → predict_from_listing.
concepts: [[concepts/telegram-bot]]
## Key symbols
- `MIN_TEXT_LEN` — 40 chars `src/krisha/text_parse.py:35`
- `PARSE_CACHE_TTL_S` — 3600 s, maxsize 512 (in-process TTLCache by sha256 of normalized text) `src/krisha/text_parse.py:47`
- `parsed_to_listing` — Gemini answer → listing dict like parse_detail `src/krisha/text_parse.py:163`
- `predict_from_text` — None if no key or not a listing `src/krisha/text_parse.py:185`
## Depends on
- [[code/src/krisha/api/cache.py]]
- [[code/src/krisha/llm_flags.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/stats.py]]
## Used by
- [[code/src/krisha/bot.py]]
- tests: [[code/tests/conftest.py]], [[code/tests/_dir|tests/test_page_bot.py]], [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_text_parse.py]], [[code/tests/_dir|tests/test_text_parse_cache.py]]
## Data / env
- Env GEMINI_API_KEY (+ GEMINI_MODEL).
## Gotchas
- Cache exists so chat users can't burn the daily Gemini quota by resending the same text.
