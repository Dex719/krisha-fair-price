---
title: Krisha Fair Price
emoji: 🏠
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.png">
  <img src="docs/logo-light.png" alt="Krisha Fair Price" width="440" />
</picture>

**Fair-price estimator for apartments in Almaty: paste a Krisha.kz link, get a verdict — good deal, fair, or overpriced**

[![CI](https://github.com/Dex719/krisha-fair-price/actions/workflows/ci.yml/badge.svg)](https://github.com/Dex719/krisha-fair-price/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-68_passed-2ea44f)
![python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![CatBoost](https://img.shields.io/badge/CatBoost-MAPE_9.5%25-FFCC00)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)

**[🚀 Live demo](https://dex719-krisha-fair-price.hf.space)** · **[📊 Almaty market dashboard](https://dex719-krisha-fair-price.hf.space/stats)** · **[🤖 Telegram bot](https://t.me/fairprice_kzbot)** · **[🧵 Threads](https://www.threads.com/@fairprice_kz)**

<img src="docs/screenshot-light.png" alt="Verdict for a listing" width="800" />

</div>

---

## ✨ What it does

1. You paste a link to an apartment listing on [Krisha.kz](https://krisha.kz).
2. The service fetches the listing and extracts ~20 features (area, rooms, floor, building age, district, residential complex, coordinates, distance to city center…).
3. A CatBoost model predicts the *fair* market price and compares it with the asking price.
4. You get a verdict (`GOOD_DEAL` / `FAIR` / `OVERPRICED`), the deviation in %, and the top factors that drive the price up or down (SHAP values).

## 🤖 Telegram bot

The same model is available as a Telegram bot: **[@krisha_fair_price_bot](https://t.me/testadsjklasdjklbot)** — send it a Krisha.kz listing link and get the verdict right in the chat (photo, fair price, top price factors).

It runs on the same FastAPI service via webhooks (`POST /tg/webhook`) — no extra server or polling worker needed. To enable it on your own deploy, set the `TELEGRAM_BOT_TOKEN` env variable; the webhook is registered automatically on startup.

## 📈 Model

Trained on **7,000+ real listings** crawled from all 8 districts of Almaty (resumable, polite crawler → SQLite).

| Metric | CatBoost | Baseline (median ₸/m² by district × rooms) |
|---|---|---|
| MAE | **7.4M ₸** | 14.2M ₸ |
| MAPE | **9.5%** | 18.0% |
| R² | **0.77** | 0.56 |

Metrics are measured on an **honest split**: relisted duplicates are deduplicated and the
train/test split is grouped by building (`GroupShuffleSplit`), so the model never sees
apartments from a test building during training. The previously reported random-split
numbers (~10.6% MAPE) were both inflated by duplicate relistings and leaky at the
building level — these are directly comparable, honest numbers.

- Target: `log1p(price)`, native categorical features (district, microdistrict, residential complex, building type).
- Spatial features: median ₸/m² over H3 hexagons (res 7 & 8), computed on train only and snapshotted to `models/spatial_ref.json` for inference.
- Anti-leakage: median ₸/m² maps are computed on the train split only.
- Explainability: per-prediction SHAP factors + global summary in [`reports/shap_summary.png`](reports/shap_summary.png).

## 🛠 Stack

`Python 3.11` · `httpx` + `BeautifulSoup4` · `SQLite` · `pandas` · `CatBoost` · `SHAP` · `FastAPI` · `vanilla JS` · `Material 3 Expressive` (light/dark) · `Chart.js` · `pytest` + `GitHub Actions` · `Hugging Face Spaces`

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

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/predict` | `{"url": "https://krisha.kz/a/show/..."}` → verdict, fair price, factors |
| `GET` | `/api/stats` | market snapshot: districts, ₸/m², price distribution |
| `GET` | `/api/health` | liveness + model status |
| `POST` | `/tg/webhook` | Telegram bot updates (secret-protected) |

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
tests/              # 25 tests: parsers (real-markup fixtures), features, DB, bot, train smoke
```

## ⚠️ Disclaimer

Educational project. Listing data belongs to Krisha.kz; crawling is deliberately gentle (randomized delays, no parallel hammering). Predictions are estimates, not financial advice.

<div align="center">

<img src="docs/screenshot-dark.png" alt="Almaty market dashboard, dark theme" width="800" />

</div>
