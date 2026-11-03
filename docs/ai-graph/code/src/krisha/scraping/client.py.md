---
type: file
path: src/krisha/scraping/client.py
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# src/krisha/scraping/client.py
purpose: Polite httpx wrapper for krisha.kz: random pause before every request, retries, 403/429/468 handling, SafeLine session reset, sticky cookies, per-attempt counters.
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `BanDetected` — raised when `ban_streak_threshold` (default 3) consecutive URLs got ONLY 403 on all attempts (issue #101); caller must abort the pass `src/krisha/scraping/client.py:16`
- `SourceUnavailable` — user path only (`raise_on_challenge=True`): page not served on any attempt and not 404 → API answers 503, not 502 `src/krisha/scraping/client.py:31`
- `ChallengeBlocked` — subclass of SourceUnavailable; at least one attempt hit a SafeLine page (468 or 403-block) `src/krisha/scraping/client.py:43`
- `CHALLENGE_STATUSES` — frozenset({468}) — SafeLine WAF code `src/krisha/scraping/client.py:64`
- `looks_like_challenge` — sniffs first 4096 bytes for `/.safeline/` or `id="slg-title"` (challenge can come with HTTP 200) `src/krisha/scraping/client.py:72`
- `StickyCookies` — process-wide cookie store of last successful user session (bucket A via `kraid` cookie); forgotten on SafeLine hit `src/krisha/scraping/client.py:78`
- `PoliteClient` — the client; ctor knobs: delay_range, max_retries, throttle_wait_s, ban_streak_threshold, timeout, challenge_wait_s, raise_on_challenge, cookie_store, on_attempt `src/krisha/scraping/client.py:117`
- `PoliteClient.get` — GET → html | None; 404 → None (never raises); 429 → escalating backoff; 468/SafeLine → drop session and retry fresh `src/krisha/scraping/client.py:251`
- `PoliteClient._reset_session` — new httpx.Client with clean cookie jar — the only thing that cures a SafeLine-flagged session `src/krisha/scraping/client.py:213`
- `PoliteClient._slow_down_if_throttled` — after 3×429 in a pass multiply delays by 1.5 once (issue #152) `src/krisha/scraping/client.py:399`
- `PoliteClient.stats` — counters http_200/403/404/429/468/other/errors + waf_block + latency p50/p90 → sweep summary JSON `src/krisha/scraping/client.py:417`
## Depends on
- [[code/src/krisha/config.py]]
## Used by
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/predict.py]]
- [[code/src/krisha/predict_gate.py]]
- [[code/src/krisha/scraping/crawler.py]]
- [[code/src/krisha/scraping/rescrape.py]]
- [[code/scripts/crawl_complexes.py]]
- tests: [[code/tests/test_api_audit_2026_10_24.py]], [[code/tests/_dir|tests/test_bot.py]], [[code/tests/_dir|tests/test_drain_mode.py]], [[code/tests/_dir|tests/test_predict_db_fallback.py]], [[code/tests/_dir|tests/test_predict_gate.py]], [[code/tests/test_rescrape.py]], [[code/tests/_dir|tests/test_rescrape_safeline_streak.py]], [[code/tests/_dir|tests/test_scraping_client.py]], [[code/tests/_dir|tests/test_session_warmup.py]], [[code/tests/test_shard_plan.py]]
## Data / env
- Outbound: https://krisha.kz (UA and timeouts from [[code/src/krisha/config.py]]: `USER_AGENT`, `REQUEST_TIMEOUT=30`, `MAX_RETRIES=3`, `REQUEST_DELAY_RANGE` from env `KRISHA_DELAY_MIN/MAX`, default 2.0–4.0 s).
## Gotchas
- Sum of `counters` must equal number of requests: rescrape budgets detail fetches from it (`fit_detail_caps`); `waf_block` is a separate sub-field of http_403, not an extra outcome.
- Crawler/sweep use default `raise_on_challenge=False` (return None); ChallengeBlocked inside a nightly pass would crash it.
- SafeLine bucket A/B is pinned by cookie `kraid` (measured 2026-09-23: 75% B on fresh sessions); retrying in the same session never helps — see `.kiro/specs/safeline-468`.
- 403 with SafeLine markup still counts toward BanDetected streak; 468 resets the streak.
