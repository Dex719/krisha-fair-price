---
type: concept
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# Scraping krisha.kz
purpose: how listings of Almaty apartments (sale `prodazha` and long-term rent `arenda`) get from krisha.kz into SQLite, incl. anti-bot (SafeLine) handling and pass planning.

## Two paths that hit krisha
- Batch sweep (nightly, GitHub Actions): [[code/src/krisha/scraping/rescrape.py]] via [[code/scripts/rescrape.py]] in [[ops/workflows/rescrape]] (sale, morning) and [[ops/workflows/rescrape-rent]] (rent, evening, DB `data/krisha_rent.db`). Default `PoliteClient` (pauses per mode, returns None on failure, raises only `BanDetected`).
- User path (web/bot, HF Space): [[code/src/krisha/predict.py]] `user_client()` — 3 attempts, 0.5–1.0 s pauses, 5 s timeout ([[code/src/krisha/predict_gate.py]]), `raise_on_challenge=True` → `SourceUnavailable`/`ChallengeBlocked` → HTTP 503; sticky cookies; session warmed at startup.
- Legacy: [[code/src/krisha/scraping/crawler.py]] (unsharded, truncated by pagination); [[code/scripts/crawl_complexes.py]] + [[code/src/krisha/scraping/complex_parser.py]] for the ЖК catalog.

## Sweep pipeline (`sweep()`, in order)
1. `init_db`, price bounds by deal (`db.price_bounds_for`), known ids.
2. Mode: backlog (detail-less lots) → [[code/src/krisha/scraping/pass_plan.py]] `choose_mode` drain (≥5000) / steady (<3000) with hysteresis; ban streak ≥2 → steady delays. Env `KRISHA_DELAY_MIN/MAX` overrides pauses.
3. Plan print (`estimate_timings` from `sweep_runs`, `plan_pass`); increment `pass_seq` (separate txn) → rotation offset ([[code/src/krisha/scraping/shard_plan.py]]).
4. Search phase: 32 shards (8 districts × rooms 1/2/3/4+, `config.ALMATY_DISTRICT_SLUGS`, `ROOM_SHARDS`), paginate up to 250 pages; card prices via [[code/src/krisha/scraping/listing_parser.py]]; shard not covered on fetch failure, antibot markers, empty first page, truncation, ban. Record `listing_shards` attribution.
5. Sightings for ALL new ids (`db.record_sighting`, issue #127): row with id/url/card price, first_seen, no details yet.
6. Detail queue (sighting-only rows, `title IS NULL`, active): quotas per shard ∝ measured stock (`largest_remainder_quotas` + `redistribute_leftover`), capped by `fit_detail_caps` to time budget (320 min) and politeness (≤10k requests, ≤0.5 rps); window = fresh-first (48 h, `fresh_share` 0.5) then sequential from per-shard cursor; detail via [[code/src/krisha/scraping/detail_parser.py]] → `db.upsert_listing` (data-contract quarantine, fingerprint, coords_approx). Failed lot stops cursor; poison after 3 failures.
7. Refresh stale details (active, scraped_at older than mode's refresh_stale_days, oldest first, `max_refresh`) — issue #102.
8. Known ids seen in search: `last_seen=now, is_active=1, delisted_at=NULL`; card price through the price contract → price_history point + listings.price if changed.
9. Delist (`is_active=0`, `delisted_at`) for lots not seen `DELIST_AFTER_DAYS=3`, only if all shards covered and delist share ≤ 30% of pre-pass active.
10. Gap/cohort bookkeeping (`data_gaps`, `first_seen_cohort` 'initial'/'gap:*' when gap ≥ 2.5 days), cursors + `sweep_shard_stats`, ban streak, `record_sweep_run`, degradation checks (found_in_search vs active-in-DB ≥ 50%), batch TVD vs stock.
11. Workflow then: summary JSON → [[code/src/krisha/daily_report.py]]; DB → release db-latest ([[code/scripts/release_upload.py]]); `sweep_state_<deal>.json` via [[code/src/krisha/scraping/sweep_state_file.py]] (always, even on failure); alerts ([[concepts/telegram-bot]]); Space restart ([[concepts/prod-runtime]]).

## Inputs / outputs
- In: krisha.kz search pages `/{deal}/kvartiry/{slug}/?das[live.rooms][]=N&page=K`, detail pages `/a/show/<id>` (inline JSON `window.data`).
- Out: tables `listings`, `price_history`, `listing_shards`, `shard_cursors`, `sweep_runs`, `sweep_shard_stats`, `sweep_state`, `data_gaps`, `detail_fetch_failures`, `parse_anomalies` ([[code/src/krisha/db.py]]); stored in [[concepts/state-storage]].

## Anti-bot (SafeLine WAF)
- HTTP 468 (or 200 with `/.safeline/` markup) = JS proof-of-work challenge; cured only by a NEW session (clean cookie jar). Bucket A/B pinned by cookie `kraid` → `StickyCookies` keeps bucket-A cookies for user path. 403 series (3 URLs, all attempts) = IP ban → `BanDetected` → early abort + admin alert. 429 → backoff, after 3×429 slow down ×1.5. Details: [[code/src/krisha/scraping/client.py]]; design notes in `.kiro/specs/safeline-468`.

## Key thresholds
DELIST_AFTER_DAYS 3 · MAX_DELIST_SHARE 0.30 · DRAIN 4500 new/800 refresh/(1.5–3.0 s) · STEADY 1500/1200/(2.0–4.0 s) · time budget 320 min · max_pages 250 · fresh window 48 h · DETAIL_POISON_ATTEMPTS 3 · STARVED_SHARD_STREAK 3 · batch TVD limit 0.20 (`validity.MAX_TEST_TVD`).

## Gotchas
- Never put bare 'captcha'/'recaptcha' in antibot signs (footer `g-recaptcha-policy`; 13-day outage from 14.07.2026).
- Detail pages open for archived lots → detail fetch/user check must not revive delisted rows (handled in `db.UPSERT_SQL*`).
- User-submitted listings are stored with `source='user'` and excluded from training.
- Sale price contract (≥5M ₸) applied to rent froze krisha_rent.db once → always `price_bounds_for(deal)`.
- Uneven collection (1000/day alphabetical, July cohorts) is why model metrics carry validity flags — see [[concepts/model-evaluation]].
