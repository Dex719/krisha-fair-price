---
type: file
path: scripts/analyze_flags.py
tags: [zone/scripts, layer/ml, layer/llm]
updated: 2026-11-03
---
# scripts/analyze_flags.py
purpose: batch LLM (Gemini) analysis of listing descriptions (roadmap stage 5) → fills `llm_flags` cache table. Resumable; cached (by `desc_hash`) rows not re-analysed. Manual only.
## CLI
- `--limit N` (max listings), `--batch 30` (descriptions per request)
## Data / env
- env `GEMINI_API_KEY` (name from `llm_flags.GEMINI_API_KEY_ENV`; required), `LLM_BATCH_DELAY` (default 7 s ≈ 8 req/min, free tier)
- reads `listings` LEFT JOIN `llm_flags` in `DB_PATH` (`data/krisha.db`), active listings first; writes `llm_flags`
- external: Gemini API
## Depends on
- [[code/src/krisha/config.py]], [[code/src/krisha/db.py]] (`get_conn`), [[code/src/krisha/llm_flags.py]] (`CACHE_SCHEMA`, `analyze_batch`, `desc_hash`, `save_flags`)
## Used by
- no workflow; manual
