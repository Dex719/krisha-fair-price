---
type: dir
path: src
tags: [zone/src]
updated: 2026-11-03
---
# src
purpose: Python source root; contains the single package `krisha` (product name FairPrice / site baǵam, bagam.info) — fair price of Almaty apartments (sale and long-term rent) from krisha.kz listings.
## Layout
- [[code/src/krisha/_dir]] — package root: data layer, ML, bot, notifications, ops helpers.
- [[code/src/krisha/scraping/_dir]] — krisha.kz HTTP client, parsers, daily sharded sweep.
- [[code/src/krisha/api/_dir]] — FastAPI app (site + API + Telegram webhook).
## Main data flow
1. Collect: [[code/src/krisha/scraping/rescrape.py]] (GitHub Actions, nightly; sale and rent) → SQLite `data/krisha.db` / `data/krisha_rent.db` via [[code/src/krisha/db.py]] → uploaded to private release db-latest ([[code/src/krisha/db_release.py]], [[code/scripts/release_upload.py]]). See [[concepts/scraping-krisha]].
2. Train: [[code/src/krisha/train.py]] (weekly retrain workflow) → models/*.cbm + meta → release model-latest. See [[concepts/sale-price-model]], [[concepts/rent-model]].
3. Serve: [[code/src/krisha/api/app.py]] on HF Space downloads DB+models at startup, answers /api/predict through [[code/src/krisha/predict_gate.py]] → [[code/src/krisha/predict.py]]. See [[concepts/web-api]], [[concepts/prod-runtime]].
4. Notify: [[code/src/krisha/bot.py]] (webhook) + [[code/scripts/send_alerts.py]] after rescrape (alerts, tracking, channel, reports). See [[concepts/telegram-bot]].
5. Persist small state: JSON files committed to private repo via [[code/src/krisha/subscriptions.py]]. See [[concepts/state-storage]].
## Install / entry points
- Package installed from `src/` (pyproject, version 1.0.0). Entry points: `uvicorn krisha.api.app:app`, `python -m krisha.db_release`, `python -m krisha.subscriptions --pull`, `python -m krisha.daily_report`, `python -m krisha.scraping.sweep_state_file`; everything else via `scripts/*.py`.
