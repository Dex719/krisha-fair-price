---
type: history
tags: [zone/docs, history]
updated: 2026-11-03
---
# readme-digest
purpose: facts claimed by README.md and docs/*.md (audits). Claims, not verified against code.
## Product / URLs
- baǵam: fair price of Almaty apartments by krisha.kz link; verdict Выгодно / В рынке / Дорого; sale + rent (₸/мес), rental yield, SHAP factors, similar lots, sale time, «подозрительно дёшево» fraud warning
- Site https://bagam.info (`/stats`, `/stats?mode=rent`, `/about`); bot https://t.me/fairprice_kzbot; Space host `dex719-krisha-fair-price.hf.space` (`/api/health` → `model_error_pct`, `revision`)
- Version badge 1.0.0; license ELv2; Python 3.11
## Architecture
- Link → parse ~20 lot attributes → CatBoost (point + MultiQuantile 10/90 with CQR conformal calibration) → verdict — [[concepts/sale-price-model]]
- Target `log1p(price)`, 47 features, native categoricals (district, microdistrict, ЖК, house type), H3 hex median ₸/м² res 7/8, OSM POI
- Validation: temporal holdout = lots first seen in last 14 days, dedup + purge of relists; model then refit on all
- Pipeline on GitHub Actions: daily scrape (fails on zero/suspicious yield), weekly retrain with quality gate, deploy only after green CI, smoke waits for deployed revision, 6-hourly health check with Telegram alert — [[concepts/ci-pipeline]]
- DB + weights in releases of private data repo with sha256, downloaded on Space start — [[concepts/state-storage]]
- Stack: httpx, BeautifulSoup4, SQLite, pandas, CatBoost, SHAP, H3, FastAPI, uvicorn, vanilla JS (README also lists GSAP), GitHub Actions, HF Spaces, Cloudflare Workers (`infra/`), pytest, Playwright — [[concepts/prod-runtime]]
- Rent DB `data/krisha_rent.db`; runtime install `pip install -e .`, train extras `.[train]`; Docker from `requirements.lock` (Linux)
- `make crawl | crawl-full | train | api (localhost:8000) | test`
## Metrics (README block, retrain 2026-10-18, from `models/model_meta.json`, synced by `scripts/sync_readme_metrics`)
- MAPE 7.22% (95% CI 7.04–7.43), MdAPE 5.13%, MAE 4.28 млн ₸, R² 0.9276; train 80 777 rows, test 19 804; `temporal_validity: true`
- Baseline median ₸/м² (district × rooms): MAPE ~16.5%. History: first model 2026-06-12 10.2% → v0.2.0 9.6% → retrain 16.08 7.5% (common test n=5 434)
- Site FAQ: rent MAPE ~11.0%
## Analytics / SEO
- Env vars (names only): `GA4_MEASUREMENT_ID`, `YANDEX_VERIFICATION`, `GOOGLE_SITE_VERIFICATION`
- GA4 events: `check_ok`, `check_error`, `check_bad_link`, `demo`, `bot_click`, `market_mode`
## docs/ audits
- `docs/SECURITY-AUDIT-2026-10-06.md`: no critical holes; secrets outside git, secret scanning + push protection, non-root Dockerfile with digest pin, SSRF closed by canonical URL from id, parametrized SQL
- `docs/SEO-AUDIT-2026-10-05.md`: 35 items; site is about Almaty; robots `Disallow: /api/`; `/docs`, `/redoc`, `/openapi.json` public; rent canonical glued to /stats
- `docs/AUDIT-2026-10-24.md`: 5-agent read-only audit; prod ok, data 23 h, MAPE 7.2%; urgent: stale e2e locators, 502 no-retry in index.html, unescaped HTML in `daily_report.py` (fixed #240–#244)
- CHANGELOG 1.0.0 says «no docs/ in repo» (working notes local) yet these 3 audit files exist in `docs/`
## Related
- [[history/changelog-timeline]], [[concepts/frontend]], [[concepts/web-api]]
