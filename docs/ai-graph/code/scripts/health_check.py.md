---
type: file
path: scripts/health_check.py
tags: [zone/scripts, layer/ops]
updated: 2026-11-03
---
# scripts/health_check.py
purpose: probe prod `/api/health` content and send a Telegram alert to admin only on state change (up / degraded / down). Stdlib only (workflow installs no project deps). Always exits 0 (a red workflow would duplicate the alert via GitHub email).
## Key symbols
- `DEFAULT_URL = "https://dex719-krisha-fair-price.hf.space/api/health"` :31, `TIMEOUT=30`, `RETRIES=2` (Space may be waking up)
- `probe(url)` :42 — `down` = no response; `degraded` if any of `status!="ok"`, `model_loaded!=True`, `tg_webhook!="ok"`, `freshness!="ok"`
- `send_telegram` :67, `build_message` :87, `main` :101
## CLI
- `--state-file health_state.json` (previous state, persisted between runs via actions/cache), `--heartbeat` (always send)
## Used by
- [[ops/workflows/space-keepalive]] (cron `30 */6 * * *`): `health_check.py --state-file health_state.json`
- tests `tests/test_health_check_script.py`
## Data / env
- env `TELEGRAM_BOT_TOKEN`, `TG_ADMIN_CHAT_ID`, optional `HEALTH_URL`; target [[concepts/prod-runtime]]
## Gotchas
- docstring once referenced `health-check.yml` which never existed — script was tested but never run until wired into space-keepalive
