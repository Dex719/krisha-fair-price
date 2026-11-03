---
type: history
tags: [zone/docs, history]
updated: 2026-11-03
---
# changelog-timeline
purpose: condensed [CHANGELOG.md] timeline, newest first. CHANGELOG rarely cites PR numbers; PRs marked (mem) come from owner memory files. Versioning: semver, MAJOR = breaking `/api/*` or page URLs.
## Unreleased (after 1.0.0)
- #240–#244, #246 (24.10) — redesign by UX audit + project audit 2026-10-24 fixes: site/bot new format with buttons; bot broadcast ≥1.1 s/msg, 429 retry; daily report escapes HTML; API rate limit per IPv6 /64, krisha refusal cached 4 s; retrain gate bootstrap by buildings, tolerance 0.15 pp (was 0.5), old quantile model kept for interval-width gate
- #235–#237 (06.10, security audit) — bot per-chat limits; no internal errors to users; all Actions `uses:` pinned to SHA, nightly `audit.yml`; worker strips HF proxy CORS headers
## 1.0.0 — 2026-10-06 (tag v1.0.0 @ a29107b)
- Own domain bagam.info; sale MAPE 7.0% temporal (was 9.5% by-building), rent model 10.9%; data/weights in private repo; deploy only after green CI
- Breaking: `/api/flags/{id}` removed (LLM flags out of runtime); `/rent` → 301 `/stats?mode=rent`; `/api/predict` adds `factors_base`, `factors_other`
- Almaty-only lots; share report → «скоро»; smooth «Наверх»; animations back under reduced-motion (#234 mem, reverted #226); no docs/ in repo; GA4 only (no Yandex Metrika); SEO by audit 05.10 + live numbers (#229 mem)
- Bot jargon-free; site redesign (#206/#207, 02.10 mem); factors sum to estimate (collinear features merged); privacy hardening; shared `static/js/site.js`
- Perf: link check ~2 s faster (median cache); webhook registration in background; GitHub outage no longer loses data / ships unchecked model
- Data: alerts only fresh lots; `scripts/backtest.py` uses prod training code; city center = Абая/Достык (was 5 km west); Cloudflare Worker domain (#211 mem); room-share not «suspiciously cheap»; /track no longer rolls back others' tracking; freshness by scraper; suspicious lots out of «Топ-5 дня»; nightly alerts fixed after model move
- Rent: rental yield for sale lots (#204 mem); rent flags equal batch vs single; rent estimate by link; rent model ₸/мес (#202/#203 mem); demo counter no longer overwrites analysis
- Storage: data, weights, bot state → private Dex719/krisha-db (#200/#201 mem); issue #74 weights out of git
- Infra/perf: usage KEEP_DAYS fixed; precompressed statics from memory; HEAD support; single predict gate `predict_gate.py`; `/api/demo` own limit (`DEMO_RATE_LIMIT_PER_WINDOW` 120); `/api/metrics`; startup warmup; `/api/health` hot path; `WEB_CONCURRENCY` 2; configurable rate limit; X-Forwarded-For spoof fixed; e2e tests (31); weekly retrain triggers HF deploy; live MAPE on site. Smoke-red fixes #194–#199 (09.2026 mem); CI on lock versions #197
## 0.5.0 — 2026-07-13
- Rebrand → baǵam, site→bot bridge; LICENSE ELv2; HF perf #58 (shap/matplotlib out of runtime); `.dockerignore`; subscriptions/tracked encrypted; short retry budgets; `db_release` sha256 check; computed current year; lifespan instead of on_event; `.env.example`
## 0.2.0 — 2026-07-03
- Rebrand → FairPrice; Railway → HF Spaces, autodeploy on push to main; Mini App CSP fix; sale-time v1; rent scraping `--deal arenda` → `krisha_rent.db`; honest validation + gate; WAL SQLite; daily rescrape sale 06:00 / rent 20:00 Almaty, weekly retrain
## 0.1.0 — 2026-06
- CatBoost fair price on 7 000+ lots, split by building; site with verdict + market dashboard, light/dark; Telegram bot with /track; resumable krisha.kz crawler → SQLite
## Related
- [[history/readme-digest]], [[history/owner-preferences]], [[concepts/frontend]]
