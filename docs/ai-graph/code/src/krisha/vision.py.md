---
type: file
path: src/krisha/vision.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/vision.py
purpose: Renovation level from listing photos via Gemini Vision (badge only, not a model feature); behind FEATURE_VISION.
## Key symbols
- `MAX_PHOTOS` — 3; MAX_PHOTO_BYTES 4 MB; PHOTO_TIMEOUT 10 s `src/krisha/vision.py:38`
- `ALLOWED_PHOTO_HOST_SUFFIX` — '.kcdn.online' (krisha CDN allowlist, SSRF guard) `src/krisha/vision.py:41`
- `LEVELS_RU` — rough/needs_repair/dated/good/premium → RU `src/krisha/vision.py:43`
- `assess_renovation` — cache (table vision_renovation) then live Gemini if live=True `src/krisha/vision.py:232`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/llm_flags.py]]
## Used by
- [[code/src/krisha/predict.py]]
- tests: [[code/tests/_dir|tests/test_vision.py]]
## Data / env
- Env GEMINI_API_KEY; table vision_renovation.
