---
type: file
path: tests/test_api_audit_2026_10_24.py
tags: [zone/tests, layer/api]
updated: 2026-11-03
---
# tests/test_api_audit_2026_10_24.py
purpose: one regression per finding of audit 24.10 section 4 (API); fast, no network.

## Key symbols (tests)
- rate limit: IPv6 folded to /64, IPv4-mapped unmapped, rotating IPv6 can't bypass, rate table hard cap + expiry; client IP only trusted via `x-bagam-proxy-key` + `x-bagam-client-ip`
- predict gate single-flight: source failure answered to all waiters with one scrape; cached failure TTL not self-extending; abandoned threads stop hitting source; no leaked locks; garbage URL rejected before cache/slots; cached exception traceback doesn't grow
- bot update runs under its own limiter; cache hit records usage without threadpool
- stale-while-revalidate: stale served while one thread refreshes; stats/heatmap/rent caches serve stale

## Depends on
- [[code/src/krisha/api/app.py]], [[code/src/krisha/api/cache.py]], [[code/src/krisha/predict_gate.py]], [[code/src/krisha/scraping/client.py]] (ChallengeBlocked, SourceUnavailable)

## Gotchas
- Proxy header trust ties to Cloudflare worker PROXY_KEY → [[concepts/prod-runtime]]. Audit PRs #240–#244.
