---
type: file
path: src/krisha/llm_flags.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/llm_flags.py
purpose: Offline LLM analysis of descriptions (Gemini, strict JSON schema): red flags/pluses cached in table llm_flags. Removed from the user path in #157; kept for offline ablation and shared Gemini constants.
## Key symbols
- `GEMINI_API_KEY_ENV` — 'GEMINI_API_KEY' `src/krisha/llm_flags.py:28`
- `GEMINI_MODEL` — env GEMINI_MODEL, default gemini-2.5-flash-lite `src/krisha/llm_flags.py:30`
- `GEMINI_URL` — generativelanguage.googleapis.com v1beta generateContent `src/krisha/llm_flags.py:31`
- `FLAGS_RU` — flag key → (kind, RU label) `src/krisha/llm_flags.py:40`
- `analyze_batch` — one Gemini request per batch; items delimited with nonce `src/krisha/llm_flags.py:253`
- `get_cached_flags_bulk` — chunked IN queries `src/krisha/llm_flags.py:142`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
## Used by
- [[code/src/krisha/text_parse.py]]
- [[code/src/krisha/vision.py]]
- [[code/scripts/analyze_flags.py]]
- tests: [[code/tests/_dir|tests/test_bot_audit_fixes.py]], [[code/tests/_dir|tests/test_llm_flags.py]]
## Data / env
- Env GEMINI_API_KEY, GEMINI_MODEL. Table llm_flags (listing_id, desc_hash). Used by [[code/scripts/analyze_flags.py]].
## Gotchas
- As features they worsened the model (R² 0.79→0.76); return only if rolling-origin backtest shows gain.
