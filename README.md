# 🏠 Krisha Fair Price

**Fair-price estimator for apartments in Almaty.** Paste a [Krisha.kz](https://krisha.kz) listing URL and get a verdict: **good deal, fair price, or overpriced** — with an ML-predicted fair price and an explanation of the main price factors.

**🔗 Live demo: [krisha-fair-price-production.up.railway.app](https://krisha-fair-price-production.up.railway.app)**
📊 Market stats dashboard: [/stats](https://krisha-fair-price-production.up.railway.app/stats)

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![CatBoost](https://img.shields.io/badge/CatBoost-gradient%20boosting-FFCC00)
![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi&logoColor=white)
![Deployed on Railway](https://img.shields.io/badge/Railway-deployed-0B0D0E?logo=railway&logoColor=white)
![Tests](https://img.shields.io/badge/tests-25%20passing-brightgreen)

## ✨ What it does

1. You paste a link to an apartment listing on Krisha.kz.
2. The service fetches the listing, extracts ~20 features (area, rooms, floor, building age, district, residential complex, coordinates, distance to city center…).
3. A CatBoost model predicts the *fair* market price and compares it with the asking price.
4. You get a verdict (`GOOD_DEAL` / `FAIR` / `OVERPRICED`), the deviation in %, and the top factors that drive the price up or down (SHAP values).

## 🤖 Telegram bot

The same model is available as a Telegram bot: **[@testadsjklasdjklbot](https://t.me/testadsjklasdjklbot)** — send it a Krisha.kz listing link and get the verdict right in the chat (photo, fair price, top price factors).

It runs on the same Railway service via webhooks (`POST /tg/webhook`) — no extra server or polling worker needed. To enable it on your own deploy, set the `TELEGRAM_BOT_TOKEN` env variable; the webhook is registered automatically on startup.

## 📈 Model

Trained on **7,000+ real listings** crawled from all 8 districts of Almaty (resumable, polite crawler → SQLite).

| Metric | CatBoost | Baseline (median ₸/m² by district × rooms) |
|---|---|---|
| MAE | **7.7M ₸** | 12.1M ₸ |
| MAPE | **10.6%** | 17.4% |
| R² | **0.78** | 0.65 |

- Target: `log1p(price)`, native categorical features (district, microdistrict, residential complex, building type).
- Anti-leakage: median ₸/m² maps are computed on the train split only.
- Explainability: per-prediction SHAP factors + global summary in [`reports/shap_summary.png`](reports/shap_summary.png).

## 🛠 Stack

Python 3.11 · httpx + BeautifulSoup4 · SQLite · pandas · CatBoost · SHAP · FastAPI · vanilla JS + Material 3 Expressive UI (light/dark) · Chart.js · pytest + GitHub Actions · Railway

## 🚀 Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

make crawl        # quick probe: ~50 listings into data/krisha.db
make crawl-full   # full crawl of Almaty (takes hours — polite delays)
make train        # train CatBoost → models/model.cbm + SHAP report
make api          # http://localhost:8000 — web UI + API
make test         # pytest
```

## 🔌 API

```bash
POST /api/predict          # {"url": "https://krisha.kz/a/show/..."} → verdict, fair price, factors
GET  /api/stats            # market snapshot: districts, ₸/m², price distribution
GET  /api/health           # liveness + model status
```

## 🗂 Project structure

```
src/krisha/
├── scraping/       # polite HTTP client (delays, retries), list & detail parsers, resumable crawler
├── features.py     # cleaning + feature engineering (floor ratio, building age, dist to center, ₸/m² maps)
├── train.py        # CatBoost training, baseline comparison, SHAP report
├── predict.py      # URL → verdict pipeline
├── bot.py          # Telegram bot: webhook updates → predict → reply
├── api/            # FastAPI app + static frontend
└── db.py           # SQLite schema, upsert by listing id
static/             # Material 3 Expressive UI: index (estimator) + stats (dashboard)
tests/              # 25 tests: parsers (real-markup fixtures), features, DB, train smoke
```

## ⚠️ Disclaimer

Educational project. Listing data belongs to Krisha.kz; crawling is deliberately gentle (randomized delays, no parallel hammering). Predictions are estimates, not financial advice.
