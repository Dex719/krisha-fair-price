---
type: file
path: infra/tg-proxy-worker.js
tags: [zone/infra, layer/edge, layer/bot]
updated: 2026-11-03
---
# infra/tg-proxy-worker.js
purpose: Cloudflare Worker relaying Telegram Bot API calls (`/bot<token>/...`) to `api.telegram.org`, because HF Spaces outbound TLS to Telegram hangs.

## Key symbols
- `SECRET_HEADER = "X-Proxy-Secret"` — `infra/tg-proxy-worker.js:38` (file line numbers: header comment `:1-36`).
- `sameSecret(a, b)` — constant-time compare.
- `fetch(request, env)`:
  1. path not starting `/bot` → 404;
  2. `env.PROXY_SECRET` set and header mismatch → 404;
  3. `env.BOT_ID` set and path not `/bot<BOT_ID>:` → 404 (full token mistakenly in BOT_ID → closed for all, fail-safe);
  4. rewrite to `https://api.telegram.org`, delete secret header, forward.

## Used by
[[code/src/krisha/bot.py]] when env `TG_API_BASE` = worker URL; `_proxy_headers` adds `X-Proxy-Secret` from `TG_PROXY_SECRET` (`src/krisha/bot.py:235`).

## Data / env
Worker: var `BOT_ID` (numeric token prefix, public), secret `PROXY_SECRET`. Space: `TG_API_BASE`, `TG_PROXY_SECRET`. [[ops/env-vars]]

## Gotchas
- Zero-downtime order: set `TG_PROXY_SECRET` in Space first (old worker forwards extra header), then `PROXY_SECRET` in worker.
- Without BOT_ID/PROXY_SECRET it is an open relay for any bot (burns free-tier quota). BOT_ID added PR #235, PROXY_SECRET later (audit 24.10 batch).
- Path check runs on normalized `url.pathname` so `/..`/`%2e%2e` traversal cannot smuggle another bot path.
- Manual redeploy in Cloudflare UI required after edits.
Related: [[ops/infra]], [[concepts/telegram-bot]].
