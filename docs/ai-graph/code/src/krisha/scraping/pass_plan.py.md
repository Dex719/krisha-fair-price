---
type: file
path: src/krisha/scraping/pass_plan.py
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# src/krisha/scraping/pass_plan.py
purpose: Pure (no I/O) planning for a sweep pass (issue #152): drain/steady modes, timing estimates from sweep_runs history, request budget fitting.
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `SweepMode` — dataclass name, delay_range, max_new, max_refresh, refresh_stale_days `src/krisha/scraping/pass_plan.py:96`
- `DRAIN_MODE` — ('drain', (1.5,3.0) s, 4500 new, 800 refresh, 30 days) — do not go below 1.5 s or above 4500 `src/krisha/scraping/pass_plan.py:117`
- `STEADY_MODE` — ('steady', (2.0,4.0) s, 1500 new, 1200 refresh, 45 days) `src/krisha/scraping/pass_plan.py:118`
- `DRAIN_ENTER_BACKLOG` — 5000 — backlog ≥ → drain `src/krisha/scraping/pass_plan.py:53`
- `DRAIN_EXIT_BACKLOG` — 3000 — backlog < → steady; between = keep previous mode (hysteresis) `src/krisha/scraping/pass_plan.py:54`
- `choose_mode` — (backlog, prev_mode) → (preset, reason) `src/krisha/scraping/pass_plan.py:133`
- `MAX_REQUESTS_PER_PASS` — 10_000 politeness ceiling `src/krisha/scraping/pass_plan.py:42`
- `MAX_MEAN_RPS` — 0.5 mean requests/s ceiling `src/krisha/scraping/pass_plan.py:43`
- `BAN_ROLLBACK_STREAK` — 2 consecutive banned passes → steady delays `src/krisha/scraping/pass_plan.py:92`
- `estimate_timings` — median per-request overhead of search/detail phases from sweep_runs; fallbacks FALLBACK_OVERHEAD_S=0.6, FALLBACK_SEARCH_PAGES=2115 `src/krisha/scraping/pass_plan.py:203`
- `plan_pass` — expected requests/minutes printed before the pass `src/krisha/scraping/pass_plan.py:249`
- `fit_detail_caps` — cut new/refresh caps to remaining time budget and politeness ceilings `src/krisha/scraping/pass_plan.py:295`
## Used by
- [[code/src/krisha/scraping/rescrape.py]]
- tests: [[code/tests/_dir|tests/test_drain_mode.py]], [[code/tests/_dir|tests/test_pass_plan.py]]
## Gotchas
- Mode is chosen by DB state (backlog), never by launch flag/calendar; explicit `--mode` overrides. Covered by [[code/tests/_dir|tests/test_pass_plan.py]] and [[code/tests/_dir|tests/test_drain_mode.py]].
