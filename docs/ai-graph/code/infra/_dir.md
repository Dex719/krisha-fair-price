---
type: dir
path: infra/
tags: [zone/infra]
updated: 2026-11-03
---
# infra/
purpose: source of the two Cloudflare Workers (deployed manually via Cloudflare UI; not built, not tested in CI, excluded from Docker context and HF snapshot).
- [[code/infra/domain-worker.js]] — bagam.info → HF Space proxy (`bagam-proxy`).
- [[code/infra/tg-proxy-worker.js]] — Telegram Bot API relay.
Overview: [[ops/infra]].
