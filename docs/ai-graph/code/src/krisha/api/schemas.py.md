---
type: file
path: src/krisha/api/schemas.py
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# src/krisha/api/schemas.py
purpose: Pydantic request/response models of the API.
concepts: [[concepts/web-api]]
## Key symbols
- `PredictRequest` — {url} `src/krisha/api/schemas.py:8`
- `Factor` — SHAP factor with impact and hint `src/krisha/api/schemas.py:17`
- `ScamRisk` — level, below_pct, reasons `src/krisha/api/schemas.py:29`
- `Analog` — similar listing `src/krisha/api/schemas.py:35`
- `Liquidity` — срок продажи `src/krisha/api/schemas.py:59`
- `RentalYield` — rent model on a sale lot `src/krisha/api/schemas.py:74`
- `PredictResponse` — full card contract incl. deal, price_period, room_share, fair_price(_low/_high), verdict, diff_pct, delisted_at, duplicate_of `src/krisha/api/schemas.py:88`
- `DemoResponse` — {url} `src/krisha/api/schemas.py:136`
- `HealthResponse` — status, model metrics, freshness, tg_webhook, revision, rent model fields `src/krisha/api/schemas.py:141`
## Used by
- [[code/src/krisha/api/app.py]]
- tests: [[code/tests/_dir|tests/test_api_schema.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_rent_serving.py]]
## Gotchas
- Contract tests: [[code/tests/_dir|tests/test_api_schema.py]]; e2e fixtures in tests/e2e/fixtures.
