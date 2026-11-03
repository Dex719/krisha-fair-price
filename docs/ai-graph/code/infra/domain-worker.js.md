---
type: file
path: infra/domain-worker.js
tags: [zone/infra, layer/edge]
updated: 2026-11-03
---
# infra/domain-worker.js
purpose: Cloudflare Worker `bagam-proxy` — serves `bagam.info` by proxying every request to the HF Space (custom domains on HF need paid PRO).

## Key symbols
- `ORIGIN = "https://dex719-krisha-fair-price.hf.space"`, `CANONICAL_HOST = "bagam.info"` — `infra/domain-worker.js:28-29`.
- `SPACE_LOCATION` regex — rewrite Space-address `Location` (any scheme/port) to `https://bagam.info<path>` — `:31`, `:124-128`.
- `PAGE_PATHS` + `isCacheablePath()` — `/`, `/stats`, `/about`, `/bot`, `/privacy`, `/terms`, `/robots.txt`, `/sitemap.xml`, `/llms.txt`, `/favicon.ico`, `/.well-known/security.txt`, `/static/*` — `:34-49`.
- `CORS_HEADERS` — stripped from upstream response — `:39-46`, `:109-111`.
- `fetch(request, env)` — `:52`:
  - canonical 301: host → bagam.info, http → https, port cleared, trailing slash removed for GET/HEAD non-`/static|/api|/tg/` paths (`:59-70`);
  - drops incoming `x-bagam-client-ip`/`x-bagam-proxy-key` (anti-spoof), sets them from `cf-connecting-ip` + `env.PROXY_KEY` when key exists (`:72-79`); sets `x-forwarded-host: bagam.info`, `x-forwarded-proto: https`;
  - upstream `fetch` with `redirect: "manual"`, cacheable → `cf.cacheEverything` TTL 200-299:120 s, 404:10 s, 5xx:0 (`:90-97`);
  - removes `link` (HF `rel=canonical` to huggingface.co), `x-proxied-host/path/replica`.

## Depends on
Space app reading `X-Bagam-Client-IP` only when `X-Bagam-Proxy-Key == PROXY_KEY` ([[code/src/krisha/api/app.py]] ~`:648`).

## Data / env
Worker secret `PROXY_KEY` (same value as Space env `PROXY_KEY`). Custom domains bagam.info + www.bagam.info. See [[ops/env-vars]].

## Gotchas
- Deployed by hand in Cloudflare UI (Workers → bagam-proxy → paste → Deploy); repo edits are inert until redeployed. Dashboard API PUT blocked by WAF.
- Introduced PR #211 (2026-10-03); canonical/Link fix PR #224; CORS strip PR #235 (security audit 2026-10-06 — HF proxy reflected any Origin, enabling distributed scraping via our Space).
- Edge cache means app usage counter sees only cache misses.
- Setup steps (zone, secret, domains, PUBLIC_BASE_URL, HTTPS/HSTS) in header comment `:13-26`.
Related: [[ops/infra]], [[concepts/prod-runtime]].
