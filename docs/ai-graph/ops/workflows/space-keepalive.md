---
type: workflow
path: .github/workflows/space-keepalive.yml
tags: [zone/ops, layer/ci, layer/monitoring]
updated: 2026-11-03
---
# .github/workflows/space-keepalive.yml ("Space keep-alive")
purpose: every 6 h ping the Space (prevents free-tier sleep) and run content health check; Telegram alert only on state change (down / up / degraded). Manual dispatch can restart the Space.

## Trigger
`cron "30 */6 * * *"` = 00:30, 06:30, 12:30, 18:30 UTC; `workflow_dispatch` input `restart` ("true"/"false", default false). No concurrency group, no permissions block. 10 min.

## Steps
1. If `restart == 'true'`: POST `https://huggingface.co/api/spaces/Dex719/krisha-fair-price/restart` (HF_TOKEN), sleep 60.
2. checkout.
3. `actions/cache/restore` `health_state.json` (key `health-state-<run_id>`, restore-keys `health-state-`).
4. setup-python 3.11 (no deps; script is stdlib-only).
5. `python scripts/health_check.py --state-file health_state.json` — GET `/api/health` (override `HEALTH_URL`), checks model loaded, webhook alive; always exits 0. [[code/scripts/health_check.py]]
6. `actions/cache/save` (always).

## Secrets
`HF_TOKEN` (restart only), `TELEGRAM_BOT_TOKEN`, `TG_ADMIN_CHAT_ID`.

## Health
All success (observed starts drift by 0–6 h from cron).

## Gotchas
- Audit 24.10: cache restore was BEFORE checkout; checkout wiped the file → "degraded" alert every 6 h, "recovered" never. Restore must stay after checkout.
- health_check.py existed with tests but was never scheduled (referenced nonexistent health-check.yml) until wired here.
- Exit 0 always: a red workflow would duplicate the Telegram alert via GitHub email.

## Related
[[ops/workflows/smoke]], [[concepts/prod-runtime]]
