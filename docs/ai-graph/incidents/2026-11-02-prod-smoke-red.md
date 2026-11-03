---
type: incident
tags: [zone/ops, layer/monitoring, layer/scraping, status/open]
updated: 2026-11-03
---
# 2026-11-02 Prod smoke red: krisha.kz put `/a/show/` behind SafeLine
purpose: why [[ops/workflows/smoke]] has been red since 2026-11-02, the evidence, and the proposed fix. Status 2026-11-03: fix in PR #247 (DB fallback + rescrape SafeLine streak + TG alerts), not merged yet; live detail fetch still blocked.

## Symptom
- Prod smoke failed 3 times in a row: run 41434138208 (2026-11-02 11:06 UTC), 41542739167 (11-02 23:06), 41622880797 (11-03 10:52). The last green runs were 41297569529 (11-01 10:27) and 41342905035 (11-01 19:40).
- Every check before the predict passes: pages, `/api/health` (freshness ok), `/api/demo`. Only the live predict fails.
- User impact: the core feature, "оценить по ссылке" on the site and in the bot, answers 503 «Источник временно не отдаёт объявление» for every listing. The bot path also uses `predict_gate.cached_predict`.

## Failing assertion
`scripts/smoke_prod.py:194` (`_post_predict`): after `PREDICT_ATTEMPTS=3` (:156) the predict got HTTP 503 three times. The `_scrape_counters_note` suffix (:201) adds `/api/metrics` counters:
```
11-02 11:06  SMOKE FAILED: POST /api/predict demo: 3 попытки подряд вернули HTTP 503. {"detail":"Источник временно не отдаёт объявление, попробуйте ещё раз"} … | /api/metrics: {'predict_challenge': 1, 'scrape_http_200': 1, 'scrape_http_403': 6, 'scrape_waf_block': 6}
11-02 23:06  … | /api/metrics: {'predict_challenge': 1, 'scrape_http_403': 9, 'scrape_waf_block': 9}
11-03 10:52  … | /api/metrics: {'predict_challenge': 2, 'scrape_http_403': 12, 'scrape_waf_block': 12}
```
The green run on 11-01 19:40 printed `✓ POST /api/predict demo`.
Server chain: `PoliteClient.get` 403 branch (`src/krisha/scraping/client.py:309`) sees a SafeLine page, so `waf_block` → `_drop_flagged_session` → retry with a fresh session. After all 3 attempts it raises `ChallengeBlocked` (:345). `api/app.py:843` then returns 503 + `Retry-After: 5` and bumps `predict_challenge`.

## Root cause
Change on the source side, not in our code. Between 2026-11-01 20:50 and 2026-11-02 09:12 UTC krisha.kz started answering every detail page `https://krisha.kz/a/show/<id>` with the SafeLine anti-bot page:
- IP of the HF Space: 403 block.
- GitHub Actions runners and a local KZ IP: HTTP 468 JS challenge.

Search pages (`/prodazha/...`, `/arenda/...`) still return 200. A fresh session, sticky cookies, a modern UA and a search-page Referer do not help. The old A/B lottery (`x-kls-bucket`, cookie `kraid`, see memory krisha-safeline-bucket) is gone: neither appears in responses any more. [[concepts/scraping-krisha]] predicted exactly this case: "если блоки начнут бить и по липкой сессии — предикт на проде ляжет, HTTP-клиентом это не лечится".

### Evidence (verified 2026-11-03)
1. **Three independent vantage points, the same picture.**
   - Daily rescrape on a GitHub runner. `/a/show/` statuses from the logs of runs 41284102358, 41406727758, 41598349782:

     | date | 200 | 404 | 468 |
     |---|---|---|---|
     | 11-01 | 3214 | 23 | 0 |
     | 11-02 | 0 | 0 | 2535 (845 lots × 3 attempts) |
     | 11-03 | 0 | 0 | 2505 (835 lots) |

     Search pages returned 200 on all three days (~2000/day).
   - Rent rescrape:
     - 11-01: 649 × 200 on `/a/show/`. The last one was at 20:50:29 UTC.
     - 11-02 23:39: 9 × 403 «блок SafeLine» → `BanDetected`.
   - HF Space (prod), live 2026-11-03 15:35–15:41 UTC. The container started ~12:47 after the rescrape restart. `/api/metrics` shows `session_warmup_failed: 2`, `scrape_http_403: 6→9`, `scrape_waf_block: 6→9`, zero `scrape_http_200`. One manual `POST /api/predict` for demo lot 1017074246 got `503`, `retry-after: 5`.
   - Local probe, 7 requests: `/a/show/1017074246` → 468 every time (233320 bytes, `/.safeline/` marker, `Set-Cookie: sl-session`). Tried a fresh session ×4, cookies plus Referer from the search page, Chrome/142 UA, iPhone Safari UA. `m.krisha.kz/a/show/…` → 468 too. `/prodazha/kvartiry/almaty/` → 200, no `x-kls-bucket`, no `kraid`.
2. **Our code did not change.**
   - Commits on main after the green 11-01 19:40 run: none. The commits since 10-25 are only `f6858209`, `02397207`, `b07500d2`, which are retrain metrics, `README`, `models/*meta*` and `reports/shap_summary.png`.
   - Prod `/api/health.revision` = `22e73ffa`, deployed 2026-10-24. No deploy-hf runs since then.
   - Dependabot ran 11-02 04:54 and opened no new PR (#245 has been open since 10-24, not merged).
3. **Not a model/data/restart issue.**
   - The smoke passed at 11-01 19:40, after the 11-01 retrain (model-2026-11-01).
   - `/api/health`: freshness ok, `data_age_hours` 2.8, `model_loaded`, `rent_model_loaded`.
   - The krisha-db `db-latest` assets were updated normally on 11-02 and 11-03.
   - The first red run's container was the one restarted by rent-rescrape at 2026-11-01 20:50:52. Its warmup still got a 200 (`scrape_http_200: 1`); by 11-02 11:06 the same process got only 403s. So nothing changed on the Space side (restart, new egress IP); the change is on the source side.

### Ruled out
- Code regression (no deploy). Dependabot. Retrain/model. Data freshness. A smoke bug: the assertion is correct, because the feature really is down.
- UA staleness: Chrome/124 was refused, but so were Chrome/142 and Safari.
- HF-IP-only ban: the GitHub runners and a KZ IP are refused too.

## Secondary findings (silent degradation masked by green runs)
- **Rescrape is green with zero detail pages.**
  - Summary lines: 11-01 `докачано деталей 2585`; 11-02 `докачано деталей 0 … очередь деталей 5264, обновлено устаревших 0`; 11-03 `0 … очередь 7544`.
  - The 468 status is not a ban, so it does not count toward `BanDetected`. The sweep spends ~3.3 h of its 300-min budget on 3×468 per lot and ends with «Бюджет времени исчерпан на докачке деталей».
  - New listings since 11-02 have no detail fields: no `lat`/`lon`, `year_built`, `ceiling`. The detail queue is growing.
  - `--fail-empty` (`scripts/rescrape.py:129`) checks only an empty search result, a ban outside the details phase, and `suspicious`. It does not check `details_fetched == 0`.
- **Ban alert never sent.** `_alert_ban` (`src/krisha/scraping/rescrape.py:194`) reads `TG_ADMIN_CHAT_ID`, but the `Rescrape` / `Rescrape rent` steps in `rescrape.yml` / `rescrape-rent.yml` do not pass `TG_ADMIN_CHAT_ID` / `TELEGRAM_BOT_TOKEN` in their env. The 11-02 23:43 log says «TG_ADMIN_CHAT_ID не задан — алерт о бане не отправлен».
- The local DB copy from 2026-10-24 already had 28796 of 47508 active rows without `lat` / `year_built` (search-only rows). Any DB fallback covers only rows whose details were fetched before 11-02.

## Timeline (UTC)
- 2026-11-01 07:16–11:28: sale rescrape, `/a/show/` 3214 × 200.
- 09:15–12:26: weekly retrain.
- 10:27 and 19:40: smoke green.
- 20:50:29: last observed `/a/show/` 200 (rent rescrape).
- 20:50:52: Space restart; warmup gets a 200.
- 2026-11-02 04:54: Dependabot run, no changes.
- 07:30: sale rescrape starts; search pages 200.
- 09:12:28: first `/a/show/` → 468; every detail request after that is 468.
- 11:06: smoke red #1 (HF IP: 403 SafeLine on the sticky session too).
- ~12:32: Space restart (sale rescrape).
- 23:06: smoke red #2.
- 23:39: rent rescrape detail → 403 SafeLine ×9 → `BanDetected`; Telegram alert skipped (no env).
- 2026-11-03 09:27: sale rescrape details again 100% 468.
- 10:52: smoke red #3.
- 12:47: Space restart; warmup fails 2/2.
- 15:41: manual predict → 503.

## Proposed fix
Restoring live detail fetch is impossible with a plain HTTP client. The SafeLine page needs JS (468) and is a hard block (403) on the HF IP. Three layers follow.

### 1. Product: degrade gracefully instead of 503 (makes smoke meaningful again)
- `src/krisha/predict.py` `predict_from_url` (:812):
  - Catch `SourceUnavailable` around `client.get(url)`.
  - Look the id up in the sale DB, then the rent DB. Pattern: `SELECT * FROM listings WHERE id = ?`, as in `bot.py:828`.
  - Accept the row only if it has detail fields (`lat IS NOT NULL AND year_built IS NOT NULL`, or whatever `parse_detail` alone fills; confirm in the parser).
  - Then call `predict_from_listing(dict(row), live_vision=False, conn=conn, deal=deal)` and tag the result `source="db"`, `as_of=row["last_seen"]`.
  - No row → re-raise as today (503).
  - Do NOT `upsert_listing` in fallback mode.
- `src/krisha/api/app.py` `_demo_pool` (:899): add `AND lat IS NOT NULL AND year_built IS NOT NULL`, so the demo lot is fallback-capable.
- `static/js/site.js` + bot card text: show «оценка по данным на <as_of>, krisha сейчас не отдаёт страницу» when `source == "db"`.
- Tests:
  - `tests/test_predict*.py`: `ChallengeBlocked` + a row in the DB gives 200 with `source == "db"`; no row gives 503.
  - `tests/test_smoke_prod.py`: see layer 2.
- `predict_gate.cached_predict` needs no change: the fallback happens inside `_run`, so a 200 gets cached by the positive TTL. Consider a shorter TTL for `source=="db"`.

### 2. Smoke: keep the signal, stop the permanent red
- `scripts/smoke_prod.py`: accept a 200 with `source == "db"`, but print a `::warning::` line with the counters from `_scrape_counters_note`. A 503 stays a failure (no fallback row means the demo pool is broken).
- Owner rule (issue #157): "постоянно красная проверка хуже отсутствующей". The outage is still reported, by the warning plus layer 3.

### 3. Pipeline: make the source outage loud where it belongs
- `src/krisha/scraping/rescrape.py` details phase: treat a streak of N lots (e.g. 3) where every attempt was SafeLine (468 or 403 + marker) like `BanDetected` (`banned_phase="details"`). This stops wasting ~3 h/night hammering the WAF, and SafeLine escalates challenge → block.
- `scripts/rescrape.py` `--fail-empty`: if details were planned and `details_fetched == 0`, `logging.error`, send a Telegram alert, and set a non-zero exit or a distinct summary flag. The DB upload must still happen (search data is valid), so put the red/alert after the upload, or alert without exiting.
- `.github/workflows/rescrape.yml` and `.github/workflows/rescrape-rent.yml`: add `TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}` and `TG_ADMIN_CHAT_ID: ${{ secrets.TG_ADMIN_CHAT_ID }}` to the env of the `Rescrape` / `Rescrape rent` steps, so `_alert_ban` actually fires.

### 4. Restore live fetch (owner decision, not verified)
- Headless browser (Playwright/Chromium) on the GitHub runner to pass the 468 JS challenge for the nightly detail fetch, then reuse `sl-session` in httpx. Heavy, but the runner has the resources.
- The HF IP gets a 403 block, not a challenge, so a browser in the Space probably will not help. A user-path live fetch would need another egress, or would stay DB-only.
- Wait-and-see: the change may be a temporary "under attack" mode at krisha. Re-probe `/a/show/` daily (layer 3 alerts make this automatic).

## How to verify
- Source state: `curl -s -o /dev/null -w '%{http_code}' -A "<USER_AGENT from krisha/config.py>" https://krisha.kz/a/show/<id>`. 468/403 = still gated; 200 = reopened. Check the rescrape log summary `докачано деталей N > 0`.
- Prod: `GET https://dex719-krisha-fair-price.hf.space/api/metrics` → `counters.scrape_http_200 > 0` and `session_warmup_ok`. Then `POST /api/predict` with the `/api/demo` URL → 200 (`source` live or db after fix 1).
- Smoke: `gh workflow run smoke.yml` (owner), or wait for the next scheduled run. Green, plus the `::warning::` while in fallback.
- Rescrape after fix 3: a night with full 468 shows early abort in details, a Telegram ban alert and the `details_fetched == 0` alert.

## Confidence
- High that the root cause is the source change: three independent IP vantage points, an exact before/after boundary in the rescrape logs, and zero code/deploy changes in the window.
- Unverified:
  - Whether a real browser passes `/a/show/`, i.e. whether humans are affected.
  - Whether the change is permanent.
  - The exact SafeLine rule (we cannot see krisha's config).
  - Why the HF IP gets 403 while the others get 468. Hypothesis: worse IP reputation, or escalation after the nightly warmup and smoke retries.

## Links
[[ops/workflows/smoke]], [[code/scripts/smoke_prod.py]], [[code/src/krisha/scraping/client.py]], [[code/src/krisha/predict.py]], [[code/src/krisha/predict_gate.py]], [[code/src/krisha/api/app.py]], [[code/src/krisha/scraping/rescrape.py]], [[code/scripts/rescrape.py]], [[ops/workflows/rescrape]], [[ops/workflows/rescrape-rent]], [[concepts/scraping-krisha]], [[concepts/prod-runtime]], [[concepts/state-storage]], [[concepts/web-api]], [[concepts/telegram-bot]]

## Fix status
- PR #247 (branch fix/source-safeline-fallback): predict_from_db fallback (`source`/`data_as_of` in /api/predict), demo pool filter, smoke warning on source=db, rescrape `DETAIL_WAF_STREAK=3` → BanDetected(details), zero-details alert, TG secrets in rescrape steps.
- Still open: live fetch of /a/show/ (needs headless browser on runner — owner decision); listings first seen after 2026-11-02 have no detail fields → predict 503.
