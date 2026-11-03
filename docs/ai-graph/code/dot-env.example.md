---
type: file
path: .env.example
tags: [zone/root, layer/config]
updated: 2026-11-03
---
# .env.example
purpose: documented template of all optional env vars (copy to `.env`, gitignored, or set in hosting settings). Sections: Telegram bot, LLM (Gemini), private data repo, DB/models, network/rate-limit, caches/workers, scraping.
Full per-variable reference, defaults, code locations and vars missing from this file: [[ops/env-vars]].
## Gotchas
- Contains the canonical bot-token rotation procedure (STATE_ENCRYPTION_KEY first) at `.env.example:69-93`.
- Keep new env vars documented here; several runtime knobs (BUILD_REVISION, BOT_UPDATE_CONCURRENCY, KRISHA_MODEL_AUTO, KRISHA_SESSION_WARMUP, KRISHA_LIVE_PAGES, SOURCE_FAILURE_TTL_S, PREDICTION_LOG, USAGE_FLUSH_SYNC) are currently not.
