---
type: ops
tags: [zone/ops, layer/infra]
updated: 2026-11-03
---
# Infra (infra/ and external services)
purpose: map of non-repo infrastructure and the two Cloudflare Worker sources kept in `infra/`.

## infra/ contents ([[code/infra/_dir]])
- [[code/infra/domain-worker.js]] — Cloudflare Worker `bagam-proxy`: `bagam.info` / `www.bagam.info` → HF Space origin. Canonical 301s, client IP pass-through with `PROXY_KEY`, edge caching of pages/static, strips HF CORS + `Link: rel=canonical` + `x-proxied-*` headers, rewrites Space `Location`.
- [[code/infra/tg-proxy-worker.js]] — Cloudflare Worker relaying `/bot<token>/...` to `api.telegram.org` (HF Spaces cannot reach Telegram reliably). Guards: `PROXY_SECRET` header check (constant-time), `BOT_ID` path prefix.
Both are deployed MANUALLY via Cloudflare dashboard UI (paste + Deploy); repo changes do nothing until the owner redeploys. Dashboard API writes are blocked by Cloudflare WAF.

## Topology
```
browser → bagam.info (Cloudflare zone, Free) → Worker bagam-proxy → https://dex719-krisha-fair-price.hf.space (HF router, 1 proxy hop) → uvicorn :7860 (2 workers)
Telegram → webhook on hf.space (SPACE_HOST) → /tg/... in app
app → TG_API_BASE (tg-proxy worker *.workers.dev) → api.telegram.org
app/Actions → api.github.com (Dex719/krisha-db releases + contents, KRISHA_DB_TOKEN)
app → krisha.kz (user predict path, SafeLine anti-bot), Gemini API (GEMINI_API_KEY)
Actions → huggingface.co (git push snapshot, restart API, HF_TOKEN)
```

## External services
| service | role | config |
|---|---|---|
| Hugging Face Spaces | prod container, Docker SDK, free cpu-basic 2 vCPU/16 GB | Space Dex719/krisha-fair-price; secrets/vars in Space settings ([[ops/env-vars]]) |
| GitHub Dex719/krisha-db (private) | DB/model/snapshot releases + bot state JSON | [[concepts/state-storage]] |
| GitHub Actions (public repo, free minutes) | all cron jobs | [[concepts/ci-pipeline]] |
| Cloudflare | DNS for bagam.info, two Workers | account owned by Dex719; UI only |
| Telegram | bot @fairprice_kzbot, channel, admin chat | [[concepts/telegram-bot]] |
| Google Gemini | LLM flags/vision/text parse | `GEMINI_API_KEY`, `GEMINI_MODEL` |
| Google Search Console / GA4 | SEO/analytics | GA4 id in static pages ([[concepts/frontend]]) |

## Gotchas
- Without `PROXY_KEY` set on both sides, rate limit on bagam.info keys on Cloudflare egress IP (all visitors share bucket).
- Worker caches `/`, `/stats`, `/about`, `/bot`, `/privacy`, `/terms`, `/robots.txt`, `/sitemap.xml`, `/llms.txt`, `/favicon.ico`, `/.well-known/security.txt`, `/static/*` for 120 s (404: 10 s, 5xx: 0). `/api/*`, `/tg/*`, bodies never cached. App's own visit counter sees only cache misses.
- robots.txt subrequest cache is per worker → Cloudflare "Custom Purge" by bagam.info URL does not purge it; wait max-age.
- Security scope incl. these workers: [[code/SECURITY]].
