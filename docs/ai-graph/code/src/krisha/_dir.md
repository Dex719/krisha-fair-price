---
type: dir
path: src/krisha
tags: [zone/src]
updated: 2026-11-03
---
# src/krisha
purpose: Package root of FairPrice. Flat modules grouped below by role; subpackages [[code/src/krisha/scraping/_dir]] and [[code/src/krisha/api/_dir]].
## Folded trivial files
- `src/krisha/__init__.py` — docstring + `__version__ = "1.0.0"` (`src/krisha/__init__.py:3`); imported by [[code/src/krisha/api/app.py]] (FastAPI version) and [[code/src/krisha/daily_report.py]].
- `src/krisha/numfmt.py` — number formatting for bot/alert texts identical to the site: `MINUS = "−"` (`:8`), `dec(value, digits)` comma decimal (`:11`), `signed` (`:17`), `pct` (`:25`). Used by [[code/src/krisha/alerts.py]], [[code/src/krisha/bot.py]], [[code/src/krisha/channel.py]], [[code/src/krisha/tracking.py]]; tested in [[code/tests/_dir|tests/test_ux_audit_fixes.py]].
## Files by role
- Config & storage: [[code/src/krisha/config.py]], [[code/src/krisha/db.py]], [[code/src/krisha/db_release.py]], [[code/src/krisha/complexes.py]]
- Features: [[code/src/krisha/features.py]], [[code/src/krisha/spatial.py]], [[code/src/krisha/geo.py]], [[code/src/krisha/zones.py]], [[code/src/krisha/rent.py]]
- Training & evaluation: [[code/src/krisha/model_spec.py]], [[code/src/krisha/train.py]], [[code/src/krisha/interval.py]], [[code/src/krisha/validity.py]], [[code/src/krisha/targets.py]], [[code/src/krisha/monitoring.py]]
- Inference & card blocks: [[code/src/krisha/predict.py]], [[code/src/krisha/predict_gate.py]], [[code/src/krisha/factor_hints.py]], [[code/src/krisha/analogs.py]], [[code/src/krisha/market.py]], [[code/src/krisha/rental_yield.py]], [[code/src/krisha/scam.py]]
- Market stats: [[code/src/krisha/stats.py]], [[code/src/krisha/forecast.py]]
- LLM (Gemini, off the hot path / behind flags): [[code/src/krisha/llm_flags.py]], [[code/src/krisha/vision.py]], [[code/src/krisha/text_parse.py]]
- Telegram & notifications: [[code/src/krisha/bot.py]], [[code/src/krisha/alerts.py]], [[code/src/krisha/tracking.py]], [[code/src/krisha/subscriptions.py]], [[code/src/krisha/channel.py]], [[code/src/krisha/report.py]], [[code/src/krisha/daily_report.py]]
- Usage / logs: [[code/src/krisha/usage.py]], [[code/src/krisha/prediction_log.py]]
## Main data flow
- listing dict (from [[code/src/krisha/scraping/detail_parser.py]]) → [[code/src/krisha/db.py]] `upsert_listing` → table `listings` (+ `price_history`).
- Training: `listings` → [[code/src/krisha/train.py]] (`clean` → `dedup_relistings` → `time_based_split` → `build_features` in [[code/src/krisha/features.py]] → CatBoost) → models/.
- Inference: URL → [[code/src/krisha/predict.py]] `predict_from_url` → `listing_to_frame` → CatBoost point + MultiQuantile+CQR → verdict → card enrichments → `PredictResponse` ([[code/src/krisha/api/schemas.py]]).
## Conventions / gotchas
- Two parallel DBs with identical schema: sale `data/krisha.db`, rent `data/krisha_rent.db`; deal string is `prodazha` / `arenda` everywhere (URL slug on krisha).
- All SQLite access through `db.get_conn`/`use_conn` (WAL, busy_timeout); direct `sqlite3.connect` guarded by [[code/tests/test_no_direct_sqlite_connect.py]].
- Optional subsystems are fail-soft (missing snapshot/model/DB → None/NaN/[]), and lazy-import heavy modules (`from krisha.bot import tg_call` inside functions).
- Many comments reference issues (#96–#190) and `.kiro/specs/*` design notes; dates are 2026.
