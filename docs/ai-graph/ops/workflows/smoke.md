---
type: workflow
path: .github/workflows/smoke.yml
tags: [zone/ops, layer/ci, layer/monitoring]
updated: 2026-11-03
---
# .github/workflows/smoke.yml ("Prod smoke")
purpose: live check of production after every deploy and twice daily: pages, API, a real `/api/predict` demo; Telegram admin alert on failure.

## Trigger
- `workflow_run` of "Deploy to HF Space" completed (job runs only if that conclusion == success).
- `cron "17 3,15 * * *"` = 03:17 and 15:17 UTC (08:17 / 20:17 Almaty). Observed start 09:38–11:06 and 19:17–23:06 UTC (GitHub schedule lag).
- `workflow_dispatch`. `concurrency: prod-smoke` (no cancel). 25 min timeout. `permissions: contents: read`.

## Steps
1. `pip install "httpx>=0.27"` only.
2. `Wait for the deployed revision` (workflow_run only): poll `$BASE_URL/api/health` `.revision` every 10 s × 90 (~15 min) until == `workflow_run.head_sha`; else fail ("деплой НЕ подтверждён"). `BASE_URL = vars.PROD_BASE_URL || https://dex719-krisha-fair-price.hf.space`.
3. `python scripts/smoke_prod.py` — page markers for `/`, `/stats`, `/about` (structural ids/data-attrs), API checks, demo predict with retries. Env knobs `PROD_BASE_URL`, `SMOKE_TIMEOUT_S`. [[code/scripts/smoke_prod.py]]
4. `Notify Telegram on failure` — curl Bot API sendMessage to `TG_ADMIN_CHAT_ID`; skipped if secrets missing.

## Secrets / vars
Secrets `TELEGRAM_BOT_TOKEN`, `TG_ADMIN_CHAT_ID`; repo variable `PROD_BASE_URL` (optional).

## Health
2026-11-02 11:06, 23:06 and 2026-11-03 10:52 UTC: FAILURE — `POST /api/predict demo: 3 попытки подряд вернули HTTP 503 "Источник временно не отдаёт объявление"` (krisha.kz source refusing the Space IP, i.e. SafeLine/anti-bot side, not app crash). Earlier runs green. See [[concepts/scraping-krisha]].

## Gotchas
- Before the revision wait, post-deploy smoke hit the OLD container (it serves 200 throughout rebuild) and "confirmed" the previous deploy. HF API `.sha` never equals GitHub sha (snapshot commit) — only `/api/health.revision` is valid.
- Do not load-test prod: HF bans by IP for load; smoke is single requests. Memory: smoke is the only regular predict in prod traffic.
- Markers are structural hooks; redesign #174 renamed `id="url"`→`id="lotUrl"` and smoke learned only after deploy.

## Related
[[ops/workflows/deploy-hf]], [[ops/workflows/space-keepalive]], [[concepts/prod-runtime]]
