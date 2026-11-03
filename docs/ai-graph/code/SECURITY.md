---
type: file
path: SECURITY.md
tags: [zone/root, layer/security]
updated: 2026-11-03
---
# SECURITY.md
purpose: vulnerability disclosure policy (Russian). Supported: 1.x latest and `main`; < 1.0.0 not.
- Report via GitHub private advisory (`/security/advisories/new`) or author Telegram @Hopepe1; mirrored at `/.well-known/security.txt` (served by app, cached by domain worker).
- In scope: bagam.info + `/api/*`, `dex719-krisha-fair-price.hf.space`, bot @fairprice_kzbot (others' subscriptions, webhook verification bypass, limit bypass), repo code incl. `infra/` workers. Examples: data/secret access, XSS/injection, client-IP spoofing (rate-limit bypass), SSRF, size/count limit bypass, token leaks.
- Out of scope: krisha.kz and third parties (HF, Cloudflare, Telegram, Google), load/DoS on prod, social engineering, raw scanner output.
- No bounty; credit on fix if wanted.
## Gotchas
- Added in PR #235 (security audit 2026-10-06). Author identity: Telegram @Hopepe1; GitHub Dex719. Operator full name intentionally absent (owner decision).
Related: [[ops/infra]], [[ops/workflows/audit]].
