# Hand-written per-file content for the src/ zone of the AI graph.
WF = lambda n: f"[[ops/workflows/{n}]]"  # noqa: E731

FILES = {}

# ----------------------------------------------------------------- scraping
FILES["src/krisha/scraping/client.py"] = dict(
    tags=["layer/scraping"],
    concepts=["scraping-krisha"],
    purpose="Polite httpx wrapper for krisha.kz: random pause before every request, retries, 403/429/468 handling, SafeLine session reset, sticky cookies, per-attempt counters.",
    symbols=[
        ("BanDetected", "raised when `ban_streak_threshold` (default 3) consecutive URLs got ONLY 403 on all attempts (issue #101); caller must abort the pass"),
        ("SourceUnavailable", "user path only (`raise_on_challenge=True`): page not served on any attempt and not 404 → API answers 503, not 502"),
        ("ChallengeBlocked", "subclass of SourceUnavailable; at least one attempt hit a SafeLine page (468 or 403-block)"),
        ("CHALLENGE_STATUSES", "frozenset({468}) — SafeLine WAF code"),
        ("looks_like_challenge", "sniffs first 4096 bytes for `/.safeline/` or `id=\"slg-title\"` (challenge can come with HTTP 200)"),
        ("StickyCookies", "process-wide cookie store of last successful user session (bucket A via `kraid` cookie); forgotten on SafeLine hit"),
        ("PoliteClient", "the client; ctor knobs: delay_range, max_retries, throttle_wait_s, ban_streak_threshold, timeout, challenge_wait_s, raise_on_challenge, cookie_store, on_attempt"),
        ("PoliteClient.get", "GET → html | None; 404 → None (never raises); 429 → escalating backoff; 468/SafeLine → drop session and retry fresh"),
        ("PoliteClient._reset_session", "new httpx.Client with clean cookie jar — the only thing that cures a SafeLine-flagged session"),
        ("PoliteClient._slow_down_if_throttled", "after 3×429 in a pass multiply delays by 1.5 once (issue #152)"),
        ("PoliteClient.stats", "counters http_200/403/404/429/468/other/errors + waf_block + latency p50/p90 → sweep summary JSON"),
    ],
    data=[
        "Outbound: https://krisha.kz (UA and timeouts from [[code/src/krisha/config.py]]: `USER_AGENT`, `REQUEST_TIMEOUT=30`, `MAX_RETRIES=3`, `REQUEST_DELAY_RANGE` from env `KRISHA_DELAY_MIN/MAX`, default 2.0–4.0 s).",
    ],
    gotchas=[
        "Sum of `counters` must equal number of requests: rescrape budgets detail fetches from it (`fit_detail_caps`); `waf_block` is a separate sub-field of http_403, not an extra outcome.",
        "Crawler/sweep use default `raise_on_challenge=False` (return None); ChallengeBlocked inside a nightly pass would crash it.",
        "SafeLine bucket A/B is pinned by cookie `kraid` (measured 2026-09-23: 75% B on fresh sessions); retrying in the same session never helps — see `.kiro/specs/safeline-468`.",
        "403 with SafeLine markup still counts toward BanDetected streak; 468 resets the streak.",
    ],
)

FILES["src/krisha/scraping/detail_parser.py"] = dict(
    tags=["layer/scraping"],
    concepts=["scraping-krisha"],
    purpose="Parse a krisha listing page (/a/show/<id>) into a flat listing dict (db.LISTING_COLUMNS + extra non-DB keys).",
    symbols=[
        ("parse_detail", "html → dict | None; main source is inline JSON `window.data.advert`, extra params from `[data-name]` HTML blocks"),
        ("extract_params", "all `data-name` → text pairs (flat.building, house.year, ceiling, flat.renovation, …) → stored as JSON in `raw_params`"),
        ("clean_description", "strips krisha translator tail («Перевод может быть неточным…»)"),
        ("_parse_floor", "floor/total_floors from params («5 из 9») or title («5/9 этаж»)"),
    ],
    gotchas=[
        "Keys `city`, `deal`, `photos` are returned but NOT persisted (not in LISTING_COLUMNS): `city` drives `predict.is_almaty`, `deal` (sectionAlias) drives `rent.detect_deal`, `photos` go to API/bot.",
        "Layout breakage: fix `[data-name]` selectors first; `window.data` is usually stable (verified June 2026).",
        "Returns None if `window.data.advert` missing/without id (crawler/sweep then count a detail failure).",
    ],
)

FILES["src/krisha/scraping/listing_parser.py"] = dict(
    tags=["layer/scraping"],
    concepts=["scraping-krisha"],
    purpose="Parse krisha search-result pages: listing ids, card prices, and next-page detection.",
    symbols=[
        ("parse_listing_ids", "unique ids from `href=\"/a/show/<id>\"` in order of appearance"),
        ("parse_listing_prices", "id → card price (₸) via structural DOM matching; None if not found"),
        ("has_next_page", "structural paginator check, not substring match (issue #99)"),
    ],
    gotchas=["Price↔id matching is structural (DOM) after a bug where prices shifted between cards; sweep relies on these card prices for price_history without opening detail pages."],
)

FILES["src/krisha/scraping/json_window.py"] = dict(
    tags=["layer/scraping"],
    concepts=["scraping-krisha"],
    purpose="Extract inline `window.<name> = {...};` JSON from krisha HTML robustly (issue #100).",
    symbols=[("extract_window_json", "find `window.<var> =` start, then `json.JSONDecoder.raw_decode` to find the real end")],
    gotchas=["Old non-greedy regex `(\\{.*?\\});` cut at the first `};` inside strings/nested objects and silently dropped listings; do not reintroduce a regex end-match."],
)

FILES["src/krisha/scraping/complex_parser.py"] = dict(
    tags=["layer/scraping"],
    concepts=["scraping-krisha", "feature-engineering"],
    purpose="Parse a residential-complex (ЖК) page /complex/show/... into a row for table `complexes` (developer, housing class, completion year, floors…).",
    symbols=[
        ("parse_complex", "html → dict for db.COMPLEX_COLUMNS; sources: window.data.complex JSON, `<dt data-name>` params, sidebar"),
        ("PARAM_MAP", "krisha param name → column (housingClass→housing_class, count.of.floors→floors_raw, home.type→material…)"),
        ("parse_completion_year", "takes the LAST year mentioned in deadline text"),
        ("parse_max_floors", "«7-9 этажей» → 9"),
    ],
)

FILES["src/krisha/scraping/crawler.py"] = dict(
    tags=["layer/scraping"],
    concepts=["scraping-krisha"],
    purpose="Legacy resumable full crawler: general search pages → ids → detail pages → SQLite. Superseded in prod by sharded rescrape.",
    symbols=[("crawl", "crawl(max_pages, max_listings, skip_known) → number of new rows; stops on BanDetected")],
    data=["Search URL: `config.SEARCH_URL` (https://krisha.kz/prodazha/kvartiry/almaty/). Writes `data/krisha.db` via db.upsert_listing."],
    gotchas=["Unsharded general search is truncated by krisha pagination (~7–8k popular listings of ~44k); production collection is [[code/src/krisha/scraping/rescrape.py]]."],
)

FILES["src/krisha/scraping/rescrape.py"] = dict(
    tags=["layer/scraping", "layer/data"],
    concepts=["scraping-krisha"],
    purpose="Daily sweep (roadmap stage 4): walk 32 search shards (8 districts × 1/2/3/4+ rooms), record sightings/prices/delists, fetch a quota-limited queue of detail pages, write pass stats.",
    symbols=[
        ("sweep", "sweep(max_pages=250, max_new_details, db_path, deal='prodazha'|'arenda', refresh_stale_days, max_refresh, time_budget_min=320, mode='auto', fresh_share=0.5) → stats dict"),
        ("shard_urls", "32 (label, url) pairs: `{BASE_URL}/{deal}/kvartiry/{district_slug}/?das[live.rooms][]=N`"),
        ("_sweep_shard", "paginate one shard → ({id: price}, covered, ban, pages); partial pages survive bans/errors but shard is 'not covered'"),
        ("_looks_like_antibot", "checks `_ANTIBOT_SIGNS` on first page of a shard"),
        ("_ANTIBOT_SIGNS", "challenge-only markers; must NOT contain bare 'captcha'/'recaptcha'"),
        ("DELIST_AFTER_DAYS", "3 — not seen in search N days → is_active=0"),
        ("DETAIL_POISON_ATTEMPTS", "3 — failed detail fetches before shard cursor may skip a lot"),
        ("MAX_DELIST_SHARE", "0.30 — mass-delist guard (normal delist 1–4%/day)"),
        ("ACTIVE_IN_DB_DROP_RATIO", "0.5 — found_in_search < 50% of active-in-DB → suspicious (issue #97)"),
        ("DEFAULT_TIME_BUDGET_MIN", "320 — soft wall-clock deadline leaving time for DB upload"),
        ("RECOVERY_GAP_DAYS", "2.5 — observation gap after which the pass is a recovery pass and new first_seen rows are marked as gap cohort (issue #156)"),
        ("STARVED_SHARD_STREAK", "3 — shard with backlog and zero quota N passes → stats['starved_shards'] (issue #168)"),
        ("_alert_ban", "Telegram alert to TG_ADMIN_CHAT_ID on early abort"),
    ],
    data=[
        "Reads/writes SQLite `data/krisha.db` or `data/krisha_rent.db` (tables listings, price_history, listing_shards, shard_cursors, sweep_runs, sweep_shard_stats, sweep_state, data_gaps, detail_fetch_failures, parse_anomalies).",
        "Local file `data/rescrape_history_<deal>.json` (parse-rate history; only useful on long-lived machines, not in Actions).",
        "Env: `TG_ADMIN_CHAT_ID` (alerts), `KRISHA_DELAY_MIN/MAX` (explicit pause override beats mode preset; empty string = not set), `TELEGRAM_BOT_TOKEN` via bot.tg_call.",
    ],
    used_extra=[WF("rescrape"), WF("rescrape-rent")],
    gotchas=[
        "Regression 14.07.2026: bare 'recaptcha' matched krisha's footer `g-recaptcha-policy` on every page → all shards 'captcha', found_in_search=0, workflow failed 13 days. Keep only challenge markers.",
        "Delist only when ALL shards covered; truncated (deeper than max_pages), empty-first-page, antibot or banned shards are 'not covered' (issue #96).",
        "Mass-delist guard denominator = active_in_db BEFORE the pass; first pass after a long blind period may legitimately trip it — do not raise thresholds (comment in code).",
        "Detail queue (title IS NULL & is_active=1) is split by largest-remainder quotas proportional to measured shard stock (issue #166); undershoot is NOT given to neighbours; per-shard cursor in shard_cursors; fresh lots (<48h, above cursor) get `fresh_share` of the window and don't move the cursor (issue #190).",
        "Cursor stops at the first failed lot of the sequential window (audit 03.10/24.10) — fixed bug where a later success skipped a failed lot until wrap-around.",
        "pass_seq is incremented at pass START in a separate transaction (issue #168) so a hard-killed runner doesn't repeat the same rotation offset.",
        "Card price goes through the same price data-contract as upsert (issue #103); detail refetch must not resurrect delisted lots (handled in db UPSERT_SQL).",
        "Mode drain/steady chosen from backlog with hysteresis (see [[code/src/krisha/scraping/pass_plan.py]]); 2 consecutive banned passes → steady delays (BAN_ROLLBACK_STREAK).",
    ],
)

FILES["src/krisha/scraping/pass_plan.py"] = dict(
    tags=["layer/scraping"],
    concepts=["scraping-krisha"],
    purpose="Pure (no I/O) planning for a sweep pass (issue #152): drain/steady modes, timing estimates from sweep_runs history, request budget fitting.",
    symbols=[
        ("SweepMode", "dataclass name, delay_range, max_new, max_refresh, refresh_stale_days"),
        ("DRAIN_MODE", "('drain', (1.5,3.0) s, 4500 new, 800 refresh, 30 days) — do not go below 1.5 s or above 4500"),
        ("STEADY_MODE", "('steady', (2.0,4.0) s, 1500 new, 1200 refresh, 45 days)"),
        ("DRAIN_ENTER_BACKLOG", "5000 — backlog ≥ → drain"),
        ("DRAIN_EXIT_BACKLOG", "3000 — backlog < → steady; between = keep previous mode (hysteresis)"),
        ("choose_mode", "(backlog, prev_mode) → (preset, reason)"),
        ("MAX_REQUESTS_PER_PASS", "10_000 politeness ceiling"),
        ("MAX_MEAN_RPS", "0.5 mean requests/s ceiling"),
        ("BAN_ROLLBACK_STREAK", "2 consecutive banned passes → steady delays"),
        ("estimate_timings", "median per-request overhead of search/detail phases from sweep_runs; fallbacks FALLBACK_OVERHEAD_S=0.6, FALLBACK_SEARCH_PAGES=2115"),
        ("plan_pass", "expected requests/minutes printed before the pass"),
        ("fit_detail_caps", "cut new/refresh caps to remaining time budget and politeness ceilings"),
    ],
    gotchas=["Mode is chosen by DB state (backlog), never by launch flag/calendar; explicit `--mode` overrides. Covered by [[code/tests/test_pass_plan.py]] and [[code/tests/test_drain_mode.py]]."],
)

FILES["src/krisha/scraping/shard_plan.py"] = dict(
    tags=["layer/scraping"],
    concepts=["scraping-krisha"],
    purpose="Pure planner for per-shard detail-fetch quotas and shard rotation (issue #166/#152/#168) so each day's batch is a city mix, not a slice.",
    symbols=[
        ("largest_remainder_quotas", "split daily cap across shards proportional to stock; sum exactly == cap (verified by brute force in #167 — don't change)"),
        ("redistribute_leftover", "2nd planner pass: leftover quota of shallow-backlog shards → measured shards with deeper backlog; unmeasured shards get nothing"),
        ("rotated", "cyclic shift of shard order"),
        ("rotation_step", "step coprime with n_shards (step +1 caused long runs of uncovered tail shards)"),
        ("rotation_offset", "offset for pass_seq"),
        ("shard_district", "district from label «Район Nк» for batch TVD control"),
    ],
    gotchas=["Before #166 the queue ordered by listings.district/rooms which are NULL for sighting-only rows → effectively ORDER BY id DESC; batch TVD vs base 0.32–0.35 and model_meta time_confounding stuck true."],
)

FILES["src/krisha/scraping/sweep_state_file.py"] = dict(
    tags=["layer/scraping", "layer/ops"],
    concepts=["scraping-krisha"],
    purpose="Persist sweep_state keys `pass_seq:<deal>` and `ban_streak:<deal>` outside SQLite as JSON asset `sweep_state_<deal>.json` in release db-latest, so failed passes still advance them (audit 24.10).",
    symbols=[
        ("KEYS", "('pass_seq', 'ban_streak')"),
        ("export_state", "DB → payload"),
        ("import_state", "merge into DB: pass_seq = max(db, file); ban_streak = value with later updated_at"),
        ("pull", "download asset from db-latest via db_release and import; fail-soft"),
        ("main", "CLI `python -m krisha.scraping.sweep_state_file {export --out|import --file|pull} [--deal] [--db]`"),
    ],
    used_extra=[WF("rescrape"), WF("rescrape-rent")],
    gotchas=["DB is uploaded to the release only after a successful pass; without this file a banned (failed) pass never incremented ban_streak, so the ban rollback never fired."],
)

# ----------------------------------------------------------------- core data
FILES["src/krisha/config.py"] = dict(
    tags=["layer/config"],
    purpose="Central constants: paths (data/, models/, reports/), krisha URLs and shard dimensions, HTTP politeness, data-contract bounds, city center/bbox, feature flags.",
    symbols=[
        ("ROOT_DIR", "repo root = parents[2] of this file"),
        ("DB_PATH", "data/krisha.db (sale)"),
        ("RENT_DB_PATH", "data/krisha_rent.db (rent)"),
        ("MODEL_PATH", "models/model.cbm; also MODEL_QUANTILE_PATH, MODEL_META_PATH, legacy MODEL_LO/HI_PATH"),
        ("RENT_MODELS_DIR", "models/rent/ with the same artifact names"),
        ("SPATIAL_REF_PATH", "models/spatial_ref.json; also OSM_POIS/OSM_ZONES/COMPLEXES snapshots in models/"),
        ("SEARCH_URL", "https://krisha.kz/prodazha/kvartiry/almaty/"),
        ("ALMATY_DISTRICT_SLUGS", "8 districts RU name → krisha slug (shard dimension 1)"),
        ("ROOM_SHARDS", "1к/2к/3к/4к+ → das[live.rooms][] values (shard dimension 2)"),
        ("DISTRICT_RU", "krisha district code (Almalinskiy_r-n…) → RU name"),
        ("listing_shard_label", "shard label «Район Nк» from detail fields"),
        ("_env_float", "float env with empty string = unset (issue #152)"),
        ("REQUEST_DELAY_RANGE", "(KRISHA_DELAY_MIN=2.0, KRISHA_DELAY_MAX=4.0)"),
        ("ALMATY_CENTER", "(43.2399, 76.9570) Абая/Достык"),
        ("LEGACY_ALMATY_CENTER", "(43.2398, 76.8898) — wrong point used before 10.2026; old metas without city_center use it"),
        ("PRICE_MIN", "sale bounds 5M..1.5B ₸; RENT_PRICE 20k..10M ₸/mo; AREA 10..500 m²; PPSM 100k..5M; RENT_PPSM 1k..40k"),
        ("ALMATY_BBOX", "lat 42.95–43.50, lon 76.55–77.25"),
        ("SHARED_PIN_MIN", "5 listings on one rounded point = ЖК pin (coords_approx)"),
        ("STALE_DELISTED_DAYS", "90 — delisted rows older than this excluded from train"),
        ("MAX_TRUSTED_DELIST_LAG_DAYS", "7 — shared by tracking (delist alerts) and market (liquidity)"),
        ("feature_vision", "env FEATURE_VISION, default off"),
        ("feature_forecast", "env FEATURE_FORECAST, default off"),
    ],
    data=["Env: `KRISHA_DELAY_MIN`, `KRISHA_DELAY_MAX`, `FEATURE_VISION`, `FEATURE_FORECAST` (truthy: 1/true/yes/on)."],
    gotchas=[
        "Feature flags are functions (read env per call) so Space restart / monkeypatch changes them without reimport.",
        "Price bounds differ by deal; applying sale bounds to rent once rejected every rent price and froze krisha_rent.db.",
    ],
)

FILES["src/krisha/db.py"] = dict(
    tags=["layer/data"],
    concepts=["scraping-krisha", "state-storage"],
    purpose="SQLite storage layer for both sale and rent DBs: schema + migrations, connection helper, listing upserts with data-contract quarantine, sightings, price history, predictions log, sweep bookkeeping, Telegram update dedup.",
    symbols=[
        ("SCHEMA", "tables listings, tg_updates, price_history, predictions, complexes, parse_anomalies, sweep_runs, data_gaps, listing_shards, shard_cursors, sweep_shard_stats, sweep_state, detail_fetch_failures"),
        ("LISTING_COLUMNS", "persisted listing fields (id, url, title, price, rooms, area, floor, …, raw_params)"),
        ("UPSERT_SQL", "scrape upsert: price/title/raw_params always overwritten, others COALESCE; does NOT touch last_seen/is_active/delisted_at on conflict (audit 24.10)"),
        ("UPSERT_SQL_USER", "source='user' upsert: never overwrites price/title/raw_params on conflict (issue #117)"),
        ("get_conn", "contextmanager: WAL, busy_timeout=5000, synchronous=NORMAL, mmap 256MB, cache 8MB; commits on exit"),
        ("use_conn", "reuse a caller-provided connection (issue #110: one conn per /api/predict)"),
        ("_migrate", "adds missing columns/indexes to old DBs (CREATE IF NOT EXISTS doesn't add columns)"),
        ("init_db", "create schema + migrate"),
        ("listing_fingerprint", "apartment fingerprint for cross-id duplicate detection"),
        ("find_duplicate_id", "other id with same fingerprint (freshest first)"),
        ("price_bounds_for", "deal → (min,max) price contract"),
        ("_validate_and_quarantine", "data-contract check; bad fields logged to parse_anomalies (issue #103)"),
        ("upsert_listing", "main write path; computes fingerprint and coords_approx; user source writes no price_history point"),
        ("record_sighting", "cheap 'seen in search' row without detail page (issue #127)"),
        ("_record_price_if_changed", "append price_history point when price changed"),
        ("log_prediction", "row into predictions (issue #128) + durable prediction_log"),
        ("log_predictions", "batch insert for alerts"),
        ("record_sweep_run", "pass counters → sweep_runs (issue #154)"),
        ("data_observed_at", "single source for 'when collector last walked the market' (health/stats freshness)"),
        ("shard_backlog_window_fresh_first", "detail window per shard: fresh (48h) above cursor first, then sequential from cursor"),
        ("zero_quota_streak", "consecutive passes a shard had zero quota with backlog"),
        ("consecutive_bans", "ban streak from sweep_state"),
        ("advance_sweep_pass_seq", "monotonic pass counter (issue #168)"),
        ("remember_update_id", "cross-worker Telegram update_id dedup in tg_updates (keeps last 5000); fail-soft True"),
    ],
    data=["Files: `data/krisha.db`, `data/krisha_rent.db` (same schema). No env vars."],
    gotchas=[
        "Detail pages open for archived lots too → detail refetch/user check must never revive delisted lots; only sighting and sweep delist set last_seen/is_active.",
        "`sqlite3.connect` outside db.py is forbidden by [[code/tests/test_no_direct_sqlite_connect.py]] (allowlist: stats.py, train.py, complexes.py).",
        "Upserts retry after `executescript(SCHEMA)+_migrate` on OperationalError — downloaded old DBs self-migrate.",
        "Index on (lat, lon) is created only if coordinates exist (old DBs).",
        "sweep_runs.started_at is millisecond precision (issue #171) — history used to overwrite itself.",
    ],
)

FILES["src/krisha/db_release.py"] = dict(
    tags=["layer/ops", "layer/data"],
    concepts=["state-storage", "prod-runtime"],
    purpose="Download/verify/unpack SQLite DBs and model archives from GitHub Releases of the PRIVATE data repo (Dex719/krisha-db): db-latest and model-latest.",
    symbols=[
        ("DATA_REPO", "env KRISHA_DATA_REPO, default Dex719/krisha-db"),
        ("RELEASE_TAG", "db-latest: assets krisha.db.gz, krisha_rent.db.gz (+ sweep_state_<deal>.json)"),
        ("MODEL_RELEASE_TAG", "model-latest: models.tar.gz, models_rent.tar.gz"),
        ("MODEL_ARTIFACT_PATHS", "model.cbm, model_quantile.cbm, model_meta.json, spatial_ref.json, complexes.json"),
        ("UPLOAD_TMP_SUFFIX", "'.uploading' — readers accept the temp asset during atomic swap"),
        ("release_asset_urls", "API urls of uploaded assets of a tag (private assets require API + token, not /releases/download/)"),
        ("_verify_checksum", "compare sha256 with `<asset>.sha256` if present; strict mode raises ChecksumUnavailable"),
        ("download", "fetch + checksum + gunzip atomically (tmp → rename)"),
        ("ensure_db", "download sale DB if missing/empty or force; fail-soft"),
        ("ensure_models", "download models if models/model.cbm missing; unpacks inside models/"),
        ("ensure_rent_db", "same for rent DB"),
        ("ensure_rent_models", "same for models/rent/"),
        ("main", "CLI `python -m krisha.db_release [--force] [--require] [--models] [--rent] [--strict-checksum]`"),
    ],
    data=["Env: `KRISHA_DB_TOKEN` (token), `KRISHA_DATA_REPO`, `KRISHA_DB_URL`/`KRISHA_MODEL_URL` (override source), `KRISHA_DB_AUTO=0`/`KRISHA_MODEL_AUTO=0` (disable auto-download). Host: api.github.com."],
    used_extra=[WF("ci"), WF("rescrape"), WF("rescrape-rent"), WF("retrain"), WF("restore-drill")],
    gotchas=[
        "Archives are unpacked inside models/ (Space image /app is not writable elsewhere).",
        "Upload side lives in [[code/scripts/release_upload.py]] (temp name → sha256 → delete old → rename; audit 24.10 replaced `gh release upload --clobber`).",
    ],
)

FILES["src/krisha/complexes.py"] = dict(
    tags=["layer/ml"],
    concepts=["feature-engineering"],
    purpose="ЖК (residential complex) reference: name normalization, snapshot to models/complexes.json for DB-less deploy, lookup of complex attributes for features.",
    symbols=[
        ("normalize_complex_name", "«ЖК \"Хан-Тенгри\"» → «хан тенгри» (strip prefixes ЖК/жилой комплекс/кг/мкр/клубный дом, quotes, separators)"),
        ("COMPLEX_ATTRS", "developer, housing_class, completion_year, construction_status, material, max_floors, apartments_count"),
        ("lookup_complex_attrs", "exact match, else retry without phase/queue suffix"),
        ("snapshot_complexes", "table complexes → models/complexes.json {name_norm: attrs}"),
        ("load_complex_lookup", "snapshot → dict; missing file → {} (features become 'unknown')"),
    ],
    data=["Reads table `complexes` in data/krisha.db (filled by [[code/scripts/crawl_complexes.py]]); writes/reads models/complexes.json (shipped in model-latest)."],
    gotchas=["Join key on listing side: raw_params['map.complex'] (~58% filled) else complex_name."],
)

# ----------------------------------------------------------------- ML
FILES["src/krisha/features.py"] = dict(
    tags=["layer/ml"],
    concepts=["feature-engineering", "sale-price-model", "rent-model", "location-features"],
    purpose="Data cleaning and feature engineering shared by training and inference (sale and rent).",
    symbols=[
        ("RAW_PARAM_CAT_MAP", "categorical features from raw_params (renovation, toilet, furniture, parking, balcony, …)"),
        ("CAT_FEATURES", "district, microdistrict, building_type, complex_name, user_type, category, raw-param cats, housing_class, developer"),
        ("NUM_FEATURES", "rooms, area, floors, building_age, ceiling, lat/lon, dist_center_km, photos_count, is_new_building, district/microdistrict_ppsm, security flags, complex nums, GEO_FEATURES, hex7_ppsm, hex8_ppsm"),
        ("EXTRA_FEATURES", "computed but NOT in model: district_mismatch, knn_ppsm, knn_n, in_golden_square, otbasy_panel_excluded"),
        ("RENT_NUM_EXTRA", "rent-only numeric (balcony_n, loggia_n, toilet_count, facilities/who flags…)"),
        ("RENT_CAT_EXTRA", "rent-only categoricals (bathroom, window_side, priv_dorm…)"),
        ("TARGET", "'log_price' = log1p(price)"),
        ("MISSING_CAT", "'unknown'"),
        ("current_year", "function, not constant (building_age)"),
        ("_sanitize_raw_numeric", "broken raw values → NaN before derived features (issue #108)"),
        ("clean", "drop rows without price/area and outside price/ppsm bounds"),
        ("compute_ppsm_maps", "median ₸/m² by district/microdistrict + global — train part only"),
        ("add_raw_param_features", "renovation/toilet/furniture/parking/balcony/security from raw_params"),
        ("add_complex_features", "join ЖК attrs via complexes lookup"),
        ("build_features", "full pipeline: resolve_zones → raw params → complex → sanitize → geo → derived → cats → ppsm maps → spatial → log_price → inf→NaN"),
        ("model_center", "center point stored in model meta (city_center) else LEGACY_ALMATY_CENTER"),
        ("listing_to_frame", "one listing dict → feature frame (predict)"),
        ("listings_to_frame", "batch version (alerts)"),
    ],
    gotchas=[
        "Coordinates sanitized BEFORE geo features; out-of-bbox coords used to yield plausible POI distances with 900 km to center.",
        "knn_ppsm/knn_n excluded: on train the neighbour is the same building, absent on test (leakage mismatch). LLM flag features removed in #157 (R² 0.79→0.76).",
        "Golden-square and Otbasy-panel features failed paired walk-forward AB (Aug 2026) — kept only as candidates/badges.",
        "price=None ('договорная') must not crash log1p → to_numeric(errors='coerce').",
        "Inference must use `model_center(meta)`, not current ALMATY_CENTER (center changed 10.2026).",
    ],
)

FILES["src/krisha/model_spec.py"] = dict(
    tags=["layer/ml"],
    concepts=["sale-price-model", "rent-model"],
    purpose="ModelSpec dataclass: what differs between sale and rent training (DB, price/ppsm bounds, features, artifact paths); one training pipeline for both.",
    symbols=[
        ("ModelSpec", "deal, label, db_path, models_dir, model/quantile/meta/spatial_ref/gate_samples/metrics_history/shap paths, num/cat features, price_bounds, ppsm_bounds"),
        ("SALE", "deal='prodazha', DB_PATH, models/, sale bounds, NUM/CAT_FEATURES"),
        ("RENT", "deal='arenda', RENT_DB_PATH, models/rent/, rent bounds, RENT_NUM/CAT_FEATURES"),
        ("spec_for", "deal → spec"),
    ],
    gotchas=["Separate artifact dir for rent so a rent retrain can never overwrite sale files."],
)

FILES["src/krisha/train.py"] = dict(
    tags=["layer/ml"],
    concepts=["sale-price-model", "rent-model", "model-evaluation", "price-interval-cqr"],
    purpose="Training pipeline (CatBoost on log1p(price)) for sale and rent: load, clean, dedup relistings, time-based split + purge, point model with early stopping, MultiQuantile+CQR interval, validity metrics, old-model comparison for the gate, refit on all data, save artifacts.",
    symbols=[
        ("TEST_WINDOW_DAYS", "14 — test = newest first_seen window"),
        ("TEST_MAX_FRACTION", "0.20 cap (issue #153); TEST_MIN_FRACTION 0.10, TEST_MIN_ROWS 500, MAX_TEST_LOOKBACK_DAYS 45"),
        ("BULK_DAY_MULTIPLIER", "3.0× median daily inflow (and ≥1500 rows) = bulk day → forced into train"),
        ("load_dataset", "SELECT * FROM listings; drops source='user' (issue #117) and stale/out-of-bbox rows"),
        ("_filter_stale_and_out_of_area", "keep active or delisted ≤ STALE_DELISTED_DAYS; drop coords outside ALMATY_BBOX"),
        ("dedup_relistings", "same fingerprint & price within 2% → one apartment (DEDUP_PRICE_TOLERANCE)"),
        ("building_groups", "building group = coords rounded ~10 m, else id"),
        ("time_based_split", "issue #104: test = most recent by first_seen with min/max fraction and bulk-day handling"),
        ("purge_leaked_train_rows", "remove train rows leaking into test via fingerprint or building group"),
        ("baseline_predict", "median ₸/m² by (district, rooms) × area"),
        ("POINT_ITERATIONS", "4000 tree ceiling (ES picks ~3400–3999)"),
        ("GATE_MIN_UNSEEN_ROWS", "200 — fewer test rows unseen by old model → gate fail-closed"),
        ("CQR_COVERAGE_MARGIN", "0.01 — calibrate at 81% to hit ~80% actual coverage"),
        ("_cqr_scale", "conformal quantile at ⌈(n+1)·c⌉/n with n = number of buildings"),
        ("fit_quantile_interval", "MultiQuantile q10+q90 (issue #132) + CQR calibration"),
        ("fit_point_model", "probe with ES on time-based val (audit 24.10) → final model on all raw_train; shared with scripts/backtest.py"),
        ("train", "full pipeline → metrics dict; writes model.cbm, model_quantile.cbm, spatial_ref.json, model_meta.json, SHAP png, metrics_history, stats snapshot (sale)"),
        ("_unseen_by_old_model", "test rows newer than old model's max_first_seen and not relistings it saw"),
        ("_typical_values", "median/mode of rent features → meta feature_defaults (used by rental_yield)"),
    ],
    data=[
        "Reads data/krisha.db or data/krisha_rent.db (spec.db_path).",
        "Writes models/{model.cbm, model_quantile.cbm, model_meta.json, spatial_ref.json, model_gate_samples.json, metrics_history.jsonl, stats.json} (rent: models/rent/…), reports/shap_summary*.png.",
    ],
    used_extra=[WF("retrain")],
    gotchas=[
        "Metrics are measured on the model WITHOUT the test window; production model is refit on ALL data (`refit_on_all`) — shipped model slightly better than reported (−0.29 pp MAPE on backtest).",
        "ppsm_maps/spatial_ref are built only on the fit part for ES probe and only on train for metrics (no target leakage); KNN excludes self on train.",
        "Old model comparison only on rows unseen by it; failure → metrics['old_model_error'] → gate blocks (issue #106).",
        "Quantile model is refit on all data with ES-found iterations; CQR scale carried over from calibration.",
        "`temporal_validity` = test representative (TVD ≤ 0.20) AND not time-confounded (30-day window) — see [[code/src/krisha/validity.py]].",
        "Uses `sqlite3.connect` directly (allowlisted).",
    ],
)

FILES["src/krisha/predict.py"] = dict(
    tags=["layer/ml", "layer/api"],
    concepts=["sale-price-model", "rent-model", "price-interval-cqr", "web-api"],
    purpose="Inference: listing URL/dict → fair price, CQR interval, verdict, SHAP factors with hints, details, market signals, rental yield, scam badge, analogs; also batch pricing for alerts.",
    symbols=[
        ("USER_COOKIES", "process-wide StickyCookies for user path"),
        ("InvalidListingUrl", "user error → 422"),
        ("ListingNotFound", "krisha 404 → 404"),
        ("ListingOutsideAlmaty", "ValueError subclass → 422"),
        ("DailyRentNotSupported", "посуточная аренда → 422"),
        ("KRISHA_URL_RE", "`krisha\\.kz/a/show/(\\d+)`"),
        ("VERDICT_THRESHOLD", "0.10 legacy flat threshold when no interval model"),
        ("load_model", "deal → (CatBoostRegressor, meta) from models/ or models/rent/"),
        ("load_interval_models", "lru_cache; MultiQuantile model or legacy model_lo/hi pair (sale only) or None"),
        ("_verdict_interval", "GOOD_DEAL if price < low, OVERPRICED if > high, else FAIR"),
        ("FACTOR_GROUPS", "collinear features merged into one factor for SHAP display"),
        ("explain_price", "SHAP → (top_factors, factors_base, factors_other) in ₸"),
        ("_apply_cqr", "new meta `cqr_scale` (width-proportional) or legacy `cqr_offset_log`"),
        ("_price_pool", "single formula for card and batch: expm1(point), interval, verdict"),
        ("predict_from_listing", "dict → full result; opens conn on the deal's DB if none"),
        ("_predict_from_listing", "assembles card: factors, details, location, price history, liquidity, rental_yield, scam_risk, renovation (flag), analogs, log_prediction"),
        ("predict_listings_batch", "alerts: one DataFrame + one CatBoost call per batch; no SHAP; doesn't write predictions"),
        ("user_client", "PoliteClient for user path: 3 attempts, delays 0.5–1.0 s, raise_on_challenge, sticky cookies"),
        ("is_almaty", "address.city, else bbox, else True"),
        ("predict_from_url", "SSRF-safe: rebuild canonical URL from id; scrape → parse → Almaty/daily-rent checks → predict → duplicate check → upsert source='user'"),
    ],
    data=["Reads models/*.cbm + meta (+ models/rent/), spatial_ref, OSM snapshots; SQLite data/krisha.db or data/krisha_rent.db (per detected deal). Outbound: krisha.kz via user_client."],
    gotchas=[
        "Deal (sale/rent) detected per listing via [[code/src/krisha/rent.py]] → different model, DB and price bounds.",
        "Room-share rent listings: verdict and diff_pct set to None, no scam badge.",
        "Rent prices rounded to 1k ₸, sale to 10k ₸ (`_round_price`).",
        "LLM description flags removed from user path in #157; vision only behind FEATURE_VISION.",
        "Legacy `cqr_offset_log` meta must keep working (old published models) — don't treat missing cqr_scale as 0.",
        "User upsert never writes price history and never revives delisted lots; result carries `delisted_at` from our DB.",
    ],
)

FILES["src/krisha/predict_gate.py"] = dict(
    tags=["layer/api"],
    concepts=["web-api", "telegram-bot"],
    purpose="Single gate to the heavy predict path for web and bot: per-listing TTL cache, single-flight, negative cache, short source-failure cache, process-wide slots, short user scrape timeout.",
    symbols=[
        ("PREDICT_CACHE_TTL_S", "env, default 600 s"),
        ("PREDICT_NEGATIVE_TTL_S", "env, default 60 s (cached errors per listing)"),
        ("SOURCE_FAILURE_TTL_S", "env, default 4 s (SourceUnavailable cached only seconds)"),
        ("PREDICT_SLOTS", "env, default 10 concurrent scrapes"),
        ("PREDICT_SLOT_WAIT_S", "env, default 15 s"),
        ("USER_SCRAPE_TIMEOUT_S", "env, default 5 s total / USER_SCRAPE_CONNECT_S 3 s"),
        ("PredictBusy", "slot not acquired in time → 503"),
        ("cache_key", "listing id (+ vision flag)"),
        ("peek", "cached result without any I/O (used before rate limit)"),
        ("cached_predict", "validate URL → cache → failure caches → TTLCache.get_or_call(_run)"),
        ("warm_session", "one user-client pass to warm sticky SafeLine session at startup"),
        ("count_scrape_attempt", "attempt outcome → metrics counters scrape_*"),
    ],
    data=["Env: PREDICT_CACHE_TTL_S, PREDICT_NEGATIVE_TTL_S, SOURCE_FAILURE_TTL_S, PREDICT_SLOTS, PREDICT_SLOT_WAIT_S, USER_SCRAPE_TIMEOUT_S, USER_SCRAPE_CONNECT_S."],
    gotchas=[
        "Scenario: a channel post brings 1000 users checking the same listing → one scrape for all.",
        "InvalidListingUrl and PredictBusy are never cached; stored exceptions are detached copies (no traceback/context).",
        "Bot also goes through this gate (it used to bypass it).",
    ],
)

FILES["src/krisha/interval.py"] = dict(
    tags=["layer/ml"],
    concepts=["price-interval-cqr"],
    purpose="finalize_interval shared by train and predict so gate coverage measures exactly the interval users see (issue #105).",
    symbols=[
        ("MIN_INTERVAL_WIDTH_LOG", "0.05 minimum width in log space used by CQR"),
        ("finalize_interval", "swap if low>high, widen to contain point; never narrows; scalar or ndarray"),
    ],
)

FILES["src/krisha/validity.py"] = dict(
    tags=["layer/ml"],
    concepts=["model-evaluation"],
    purpose="Honest evaluation helpers (issue #158): test representativeness (TVD), cluster-bootstrap MAPE CI by building, time-confounding check.",
    symbols=[
        ("BOOTSTRAP_N", "2000 resamples"),
        ("MIN_CLUSTERS_FOR_CI", "30"),
        ("MAX_TEST_TVD", "0.20 — max TVD for representative test / non-confounded day mix; reused by rescrape batch control"),
        ("total_variation_distance", "TVD between category distributions"),
        ("representativeness", "per-column TVD of test vs full sample + worst"),
        ("mape", "MAPE with maximum(y,1) guard"),
        ("cluster_bootstrap_ci", "percentile CI of MAPE resampling buildings"),
        ("time_confounding", "weighted per-day TVD of district mix (days with ≥ MIN_ROWS_PER_DAY=30) > MAX_TEST_TVD → confounded"),
    ],
    gotchas=["Context: early collection was capped 1000/day and went alphabetically by district → test was 78.7% Алмалинский (TVD 0.46); metric not readable as city-wide error while confounded."],
)

FILES["src/krisha/targets.py"] = dict(
    tags=["layer/ml"],
    concepts=["model-evaluation"],
    purpose="Experimental target modes for the walk-forward bench (issue #131): price, ppsm, index_residual; used only by scripts/backtest.py.",
    symbols=[
        ("TARGET_MODES", "('price', 'ppsm', 'index_residual')"),
        ("build_city_index", "weekly median ₸/m² over fixed hex7 basket from train"),
        ("add_target_column", "adds target col for mode"),
        ("predict_price", "inverse transform to price"),
        ("freshness_weight", "0.5 ** (age_days / half_life)"),
    ],
    gotchas=["Production target is still log1p(price) (`features.TARGET`); this module is bench-only."],
)

FILES["src/krisha/spatial.py"] = dict(
    tags=["layer/ml"],
    concepts=["location-features", "feature-engineering"],
    purpose="Spatial price features: median ₸/m² per H3 hex (res 7 ~5 km², res 8 ~0.7 km²) and KNN spatial lag; reference built on train and saved to spatial_ref.json.",
    symbols=[
        ("HEX_RES_COARSE", "7; HEX_RES_FINE 8; HEX_MIN_N 8"),
        ("KNN_K", "15 neighbours within KNN_MAX_KM=1.0, KNN_MIN_N=3"),
        ("SPATIAL_FEATURES", "hex7_ppsm, hex8_ppsm, knn_ppsm, knn_n"),
        ("build_spatial_ref", "train df → points + hex medians"),
        ("self_indices_for", "positions of train rows in ref (to exclude self from KNN)"),
        ("load_spatial_ref", "cached load; missing → None (features fall back)"),
        ("add_spatial_features", "fallback chain fine → coarse → district_ppsm"),
    ],
    data=["models/spatial_ref.json (sale), models/rent/spatial_ref.json (rent)."],
    gotchas=["Rent must load the rent spatial_ref (bug fixed: rent used the sale ref)."],
)

FILES["src/krisha/geo.py"] = dict(
    tags=["layer/ml"],
    concepts=["location-features"],
    purpose="OSM POI features (roadmap stage 3): distances to metro/school/kindergarten/park/supermarket/bus stop/big road/industrial, walk_score, and the «Локация» card block.",
    symbols=[
        ("POI_DISTANCE_FEATURES", "category → dist_*_km feature"),
        ("GEO_FEATURES", "dist_* + walk_score"),
        ("PoiIndex", "KD-trees per category on local equirectangular projection"),
        ("load_poi_index", "models/osm_pois.json → index; missing → None (NaN features)"),
        ("walk_score", "0–100, per-category linear decay to thresholds"),
        ("add_geo_features", "adds features; no coords/snapshot → NaN"),
        ("build_location_details", "card block with nearest POIs and warnings (BIG_ROAD_WARN_KM 0.15, INDUSTRIAL_WARN_KM 0.4)"),
    ],
    data=["models/osm_pois.json built once by [[code/scripts/fetch_osm_pois.py]] (Overpass API)."],
)

FILES["src/krisha/zones.py"] = dict(
    tags=["layer/ml"],
    concepts=["location-features"],
    purpose="Fix district/microdistrict from coordinates using OSM polygons; detect shared ЖК pins.",
    symbols=[
        ("ZoneIndex", "index over models/osm_zones.json: district (point-in-polygon, admin_level=6), microdistrict (polygons or nearest centroid ≤ 1.2 km), shared_pin_count"),
        ("load_zone_index", "missing snapshot → None, everything unchanged"),
        ("resolve_zones", "repairs district/microdistrict + flag district_mismatch"),
        ("approximate_pin_note", "«координаты примерные» badge when point is a ЖК pin"),
    ],
    data=["models/osm_zones.json built by [[code/scripts/fetch_osm_zones.py]]."],
    gotchas=["krisha microdistrict empty in ~57% of listings; ~15% sit on one shared ЖК point."],
)

FILES["src/krisha/rent.py"] = dict(
    tags=["layer/ml"],
    concepts=["rent-model"],
    purpose="Rent-specific helpers shared by training and inference: deal detection, daily-rent detection, room-share (подселение) detection/filtering.",
    symbols=[
        ("detect_deal", "explicit sectionAlias → price < 3M → rent param keys → sale param keys → price < 5M → sale"),
        ("is_daily_rent", "посуточ/за сутки/почасов/тәулік in title/params, or in description with price < 45k"),
        ("ROOM_SHARE_RE", "regex for подселение/койко-место/комната"),
        ("is_room_share", "description match"),
        ("drop_room_shares", "removes room shares from rent training (~0.7% of base, ~78% error)"),
    ],
)

FILES["src/krisha/rental_yield.py"] = dict(
    tags=["layer/ml"],
    concepts=["rent-model"],
    purpose="Gross rental yield for a SALE listing: run the rent model on the sale lot (mapped to rent vocabulary, missing rent features from meta feature_defaults) → expected rent + interval, yield = 12×rent/price, plus district benchmark.",
    symbols=[
        ("as_rental", "sale listing → rent-page vocabulary (renovation, toilet, balcony)"),
        ("ROUGH_FINISH", "черновая/свободная планировка → estimate 'after renovation' (assumes_renovation)"),
        ("estimate", "(listing, sale_price) → dict | None"),
        ("district_yield", "12 × rent ₸/m² / sale ₸/m² medians, last DISTRICT_WINDOW_DAYS=60, min DISTRICT_MIN_N=30"),
    ],
    data=["Rent model + rent spatial_ref; both DBs for district medians (cached 6 h)."],
    gotchas=["Gross yield by asking prices: no tax, vacancy, utilities, repairs."],
)

FILES["src/krisha/scam.py"] = dict(
    tags=["layer/ml"],
    concepts=["price-interval-cqr"],
    purpose="«Подозрительно дёшево» badge (issue #157): price below CQR-calibrated lower bound; level high only if ≥15% below AND fresh.",
    symbols=[
        ("DEEP_BELOW_PCT", "15.0"),
        ("FRESH_DAYS", "7"),
        ("assess_scam_risk", "(fair_low, actual_price, days_on_market) → {level, below_pct, reasons} | None"),
    ],
    gotchas=["Replaced an ad-hoc score system (regexes on «задаток», photo counts) that flagged honest listings."],
)

FILES["src/krisha/analogs.py"] = dict(
    tags=["layer/ml"],
    concepts=["web-api"],
    purpose="«Похожие квартиры»: kNN among active listings (same rooms, area ±25%) ranked by weighted area/geo/year distance.",
    symbols=[
        ("MAX_ANALOGS", "5"),
        ("AREA_TOLERANCE", "0.25"),
        ("find_analogs", "subject → top-k analog dicts; errors → [] (fail-soft)"),
    ],
)

FILES["src/krisha/factor_hints.py"] = dict(
    tags=["layer/ml"],
    concepts=["sale-price-model"],
    purpose="Human hints for each SHAP factor with real market stats from the DB (median ₸/m² slices), cached per process.",
    symbols=[
        ("MARKET_STATS_TTL_S", "6 h cache"),
        ("market_stats", "median ₸/m² by slices of active listings; {} if no DB"),
        ("build_factor_hints", "adds `hint` to each factor"),
    ],
    gotchas=["Must not create an empty DB when it doesn't exist (fixed)."],
)

FILES["src/krisha/market.py"] = dict(
    tags=["layer/ml", "layer/data"],
    concepts=["web-api"],
    purpose="Market signals for the card (roadmap stage 4): price history points, days on market, liquidity («срок продажи») from delisted similar listings.",
    symbols=[
        ("MIN_DELISTED_SAMPLE", "15; MIN_BAND_SAMPLE 20; MIN_CITY_SAMPLE 30; NEAR_BAND_PCT 5.0"),
        ("price_history_points", "price_history rows for the chart"),
        ("days_on_market", "first_seen → last_seen/now"),
        ("liquidity_estimate", "median days to delist of segment (district+rooms) with price band by diff_pct from predictions log"),
    ],
    gotchas=["Liquidity uses only organic rows: is_active=0, delisted_at set, days ≥ 1, `first_seen_cohort IS NULL` (cohorts 'initial' and 'gap:*' are set by rescrape.sweep — their first_seen is our discovery date, not publication) and trusted delist lag ≤ MAX_TRUSTED_DELIST_LAG_DAYS (issue #156)."],
)

FILES["src/krisha/stats.py"] = dict(
    tags=["layer/data"],
    concepts=["web-api"],
    purpose="Market statistics for /api/stats, /api/stats/rent, /api/heatmap and reports; falls back to models/stats.json snapshot when DB is absent.",
    symbols=[
        ("STATS_SNAPSHOT_PATH", "models/stats.json"),
        ("_weekly_trend", "weekly median ₸/m² of listings active that week (also used by forecast/report)"),
        ("data_updated_at", "updated_at = collector freshness (db.data_observed_at)"),
        ("compute_stats", "sale stats; raises FileNotFoundError if no DB"),
        ("compute_rent_stats", "rent medians by district/rooms, distribution, ₸/m² trend, district yields"),
        ("snapshot_stats", "write models/stats.json (called by train for sale)"),
        ("heatmap_points", "~400 m grid of ₸/m²"),
        ("get_stats", "live DB else snapshot"),
    ],
    gotchas=["Uses direct sqlite3.connect (allowlisted, not on predict hot path)."],
)

FILES["src/krisha/forecast.py"] = dict(
    tags=["layer/ml"],
    concepts=["web-api"],
    purpose="Naive 3/6-month ₸/m² forecast per city/district: OLS on weekly medians (behind FEATURE_FORECAST, off by default).",
    symbols=[
        ("HORIZONS", "{'m3': 13, 'm6': 26} weeks"),
        ("MIN_POINTS", "6; MIN_WEEKS_M6 12; MIN_N_DISTRICT 30"),
        ("build_forecast", "{city, districts, disclaimer}"),
    ],
    gotchas=["Calendar-gap slope bug fixed in #109."],
)

FILES["src/krisha/llm_flags.py"] = dict(
    tags=["layer/ml"],
    purpose="Offline LLM analysis of descriptions (Gemini, strict JSON schema): red flags/pluses cached in table llm_flags. Removed from the user path in #157; kept for offline ablation and shared Gemini constants.",
    symbols=[
        ("GEMINI_API_KEY_ENV", "'GEMINI_API_KEY'"),
        ("GEMINI_MODEL", "env GEMINI_MODEL, default gemini-2.5-flash-lite"),
        ("GEMINI_URL", "generativelanguage.googleapis.com v1beta generateContent"),
        ("FLAGS_RU", "flag key → (kind, RU label)"),
        ("analyze_batch", "one Gemini request per batch; items delimited with nonce"),
        ("get_cached_flags_bulk", "chunked IN queries"),
    ],
    data=["Env GEMINI_API_KEY, GEMINI_MODEL. Table llm_flags (listing_id, desc_hash). Used by [[code/scripts/analyze_flags.py]]."],
    gotchas=["As features they worsened the model (R² 0.79→0.76); return only if rolling-origin backtest shows gain."],
)

FILES["src/krisha/vision.py"] = dict(
    tags=["layer/ml"],
    purpose="Renovation level from listing photos via Gemini Vision (badge only, not a model feature); behind FEATURE_VISION.",
    symbols=[
        ("MAX_PHOTOS", "3; MAX_PHOTO_BYTES 4 MB; PHOTO_TIMEOUT 10 s"),
        ("ALLOWED_PHOTO_HOST_SUFFIX", "'.kcdn.online' (krisha CDN allowlist, SSRF guard)"),
        ("LEVELS_RU", "rough/needs_repair/dated/good/premium → RU"),
        ("assess_renovation", "cache (table vision_renovation) then live Gemini if live=True"),
    ],
    data=["Env GEMINI_API_KEY; table vision_renovation."],
)

FILES["src/krisha/text_parse.py"] = dict(
    tags=["layer/bot", "layer/ml"],
    concepts=["telegram-bot"],
    purpose="Estimate from pasted listing text: Gemini extracts params with strict JSON schema → listing dict → predict_from_listing.",
    symbols=[
        ("MIN_TEXT_LEN", "40 chars"),
        ("PARSE_CACHE_TTL_S", "3600 s, maxsize 512 (in-process TTLCache by sha256 of normalized text)"),
        ("parsed_to_listing", "Gemini answer → listing dict like parse_detail"),
        ("predict_from_text", "None if no key or not a listing"),
    ],
    data=["Env GEMINI_API_KEY (+ GEMINI_MODEL)."],
    gotchas=["Cache exists so chat users can't burn the daily Gemini quota by resending the same text."],
)

# ----------------------------------------------------------------- bot & notifications
FILES["src/krisha/bot.py"] = dict(
    tags=["layer/bot"],
    concepts=["telegram-bot"],
    purpose="Telegram bot on the same FastAPI app via webhook POST /tg/webhook: URL evaluation, free-text evaluation, /track, /alerts, inline buttons, per-chat rate limits, webhook setup/self-healing, shared tg_call.",
    symbols=[
        ("TG_API", "https://api.telegram.org; override via TG_API_BASE (proxy)"),
        ("WEBHOOK_PATH", "/tg/webhook; allowed updates: message, callback_query"),
        ("bot_token", "env TELEGRAM_BOT_TOKEN (absent → bot disabled)"),
        ("webhook_secret", "sha256('kfp:'+token)[:32] = X-Telegram-Bot-Api-Secret-Token"),
        ("_get_tg_client", "httpx client forced to IPv4 (HF Spaces IPv6 TLS hangs), retries=2"),
        ("tg_call", "POST Bot API method; never raises; token masked in logs"),
        ("format_reply", "HTML answer for a prediction result"),
        ("reply_keyboard", "buttons track:<id>, sim:<id>, site link `bagam.info/#check=<id>`"),
        ("_BOT_LIMITS", "per-chat per-minute buckets: msg 12, text 3 (Gemini), state 5 (envs BOT_*_LIMIT_PER_MIN)"),
        ("handle_update", "/start [payload], /help, /alerts*, /track, /untrack, URL → _evaluate_url, else free text"),
        ("_evaluate_url", "predict_gate.cached_predict(live_vision=True) with mapped error messages"),
        ("_handle_callback", "actions track/untrack/sim/redo/list/demo/help/alerts"),
        ("_handle_start_payload", "deep links track_<id>, market_<slug>"),
        ("_handle_track_command", "/track [url], /untrack <url>|all (max 10 per chat)"),
        ("setup_webhook", "register webhook at startup (background thread)"),
        ("webhook_status", "self-healing check from /api/health (hourly; retry 10 min)"),
    ],
    data=["Env: TELEGRAM_BOT_TOKEN, TG_API_BASE, TG_PROXY_SECRET (header X-Proxy-Secret to infra/tg-proxy-worker.js), SPACE_HOST, PUBLIC_BASE_URL, RAILWAY_PUBLIC_DOMAIN, BOT_RATE_LIMIT_PER_MIN, BOT_TEXT_LIMIT_PER_MIN, BOT_STATE_LIMIT_PER_MIN. Hosts: Telegram Bot API (or proxy), krisha.kz via predict."],
    gotchas=[
        "Never send `str(exc)` to users (fixed); errors map to MSG_* texts; unexpected exceptions still answer the user.",
        "Webhook base: hosting domain (SPACE_HOST) preferred; PUBLIC_BASE_URL only without it.",
        "Rate limits checked BEFORE usage.record_event.",
        "Many modules import `tg_call` lazily from here (rescrape, monitoring, usage…) to avoid heavy imports.",
    ],
)

FILES["src/krisha/subscriptions.py"] = dict(
    tags=["layer/bot", "layer/ops"],
    concepts=["telegram-bot", "state-storage"],
    purpose="Alert subscriptions + generic JSON state persistence: local file + commit via GitHub Contents API to private STATE_REPO, Fernet encryption, merge-on-write, pull at startup.",
    symbols=[
        ("STATE_REPO", "env KRISHA_STATE_REPO, default Dex719/krisha-db"),
        ("STATE_FILES", "subscriptions, tracked, usage_stats, prediction_log, channel_posted, alerted_ids, alerted_chats (.json)"),
        ("STATE_LOCK", "process RLock for state edits"),
        ("_fernet", "key from STATE_ENCRYPTION_KEY else TELEGRAM_BOT_TOKEN; none → plaintext (local dev)"),
        ("load_json_state", "decrypts {'_encrypted': …} wrapper or plain JSON"),
        ("parse_filters", "/alerts_on tail: 1–6 rooms, ≥7 = max price in mln ₸, word = district"),
        ("save_json_state", "write local atomically + push to GitHub (merge with remote first)"),
        ("_merge_remote", "top-level key union with deleted/touched keys, update_only (issue #111)"),
        ("_push_to_github", "PUT contents with retries (PUSH_MAX_ATTEMPTS=3), admin alert on failure"),
        ("pull_state", "download STATE_FILES into data/ at startup / before alerts"),
        ("main", "CLI `python -m krisha.subscriptions --pull`"),
    ],
    data=["Files data/*.json (STATE_FILES). Env: KRISHA_DB_TOKEN (preferred; legacy GITHUB_PAT/GITHUB_TOKEN), KRISHA_STATE_REPO, STATE_ENCRYPTION_KEY, TELEGRAM_BOT_TOKEN, STATE_FORCE_OVERWRITE, TG_ADMIN_CHAT_ID. Host api.github.com."],
    used_extra=[WF("rescrape"), WF("rescrape-rent")],
    gotchas=[
        "Hosting disk is wiped on restart: without token state lives until next restart.",
        "chat_id is PII → whole file encrypted.",
        "Writes merge only own touched keys over server copy, so a stale Actions copy can't roll back bot edits (fix 5e3c58f).",
        "FAIL-CLOSED (issue #150): if the remote file exists but cannot be read/decrypted (key changed, broken JSON) the push is cancelled instead of overwriting all subscribers; emergency override env STATE_FORCE_OVERWRITE=1.",
    ],
)

FILES["src/krisha/tracking.py"] = dict(
    tags=["layer/bot"],
    concepts=["telegram-bot"],
    purpose="/track: per-chat watched listings; after each rescrape compare with DB and emit price-change / delisted messages (sale and rent separately).",
    symbols=[
        ("TRACKED_PATH", "data/tracked.json {chat_id: {listing_id: {price, title, since, deal?}}}"),
        ("MAX_TRACKED_PER_CHAT", "10"),
        ("lot_deal", "missing 'deal' = sale"),
        ("add_tracked", "(ok, reason); refreshes from server first"),
        ("remove_tracked", "one or all"),
        ("check_tracked_updates", "→ [(chat_id, html)]; only_chats = persist only delivered chats"),
        ("_lot_event", "price change or delist (trusted lag ≤ MAX_TRUSTED_DELIST_LAG_DAYS)"),
    ],
    gotchas=["Persist state only after successful delivery (otherwise user never learns about the change)."],
)

FILES["src/krisha/alerts.py"] = dict(
    tags=["layer/bot"],
    concepts=["telegram-bot"],
    purpose="Daily alerts after rescrape: new listings (window 26 h) priced in batch; GOOD_DEAL ones sent to subscribers matching filters, with delivery log and pacing.",
    symbols=[
        ("ALERT_WINDOW_HOURS", "26"),
        ("MAX_DEALS_PER_CHAT", "5"),
        ("ALERTED_PATH", "data/alerted_ids.json (dedup; keep 2000)"),
        ("new_listings", "active, price>0, area>0, first_seen in window OR scraped_at in window and first_seen ≤ FRESH_DAYS"),
        ("find_good_deals", "batch pricing (ALERT_BATCH_SIZE=500) + log_predictions; suspicious deals sorted last"),
        ("AlertsPricingFailed", "window non-empty but nothing priced → error, not silence"),
        ("deliver", "sendMessage with one retry on 429 (retry_after ≤ 30 s)"),
        ("_Pacer", "≥1.1 s per chat, 0.04 s global"),
        ("send_alerts", "header + one card per deal with buttons"),
    ],
    gotchas=[
        "01–03.10: models moved to private release, job didn't download them, alerts silently empty → now raises AlertsPricingFailed.",
        "Per-lot full cards took 80 min on 4–7k lots and blocked DB upload (2026-09-20) → batch pricing.",
        "Gone chats (blocked/deleted) are forgotten; group→supergroup migration moves subscription.",
    ],
)

FILES["src/krisha/channel.py"] = dict(
    tags=["layer/bot"],
    concepts=["telegram-bot"],
    purpose="Public Telegram channel digest «Топ-5 выгодных» after nightly rescrape.",
    symbols=[
        ("CHANNEL_ENV", "TG_CHANNEL_ID"),
        ("POSTED_PATH", "data/channel_posted.json (keep 500)"),
        ("post_channel_digest", "picks unposted deals, posts, persists ids"),
    ],
)

FILES["src/krisha/report.py"] = dict(
    tags=["layer/bot"],
    concepts=["telegram-bot"],
    purpose="Monthly market report (1st of month) to admin and channel from compute_stats.",
    symbols=[("build_monthly_report", "HTML from stats"), ("send_monthly_report", "to TG_ADMIN_CHAT_ID and TG_CHANNEL_ID")],
)

FILES["src/krisha/daily_report.py"] = dict(
    tags=["layer/ops", "layer/bot"],
    concepts=["scraping-krisha"],
    purpose="Daily admin report after rescrape (morning sale, evening rent) with OK/PROBLEM verdict from pipeline invariants, DB size, model metrics and usage.",
    symbols=[
        ("SALE_SUMMARY_PATH", "/tmp/rescrape_stats.json (rescrape --summary-json); rent /tmp/rescrape_rent_stats.json"),
        ("QUEUE_GROWTH_STREAK", "3; CAP_HIT_STREAK 3; MAX_RETRAIN_AGE_DAYS 8; UNATTRIBUTED_ALERT_MIN 100 / SHARE 0.05"),
        ("_invariants", "'pipeline alive' checks; shown only when failed"),
        ("build_daily_report", "scope sale|rent"),
        ("send_daily_report", "to TG_ADMIN_CHAT_ID"),
        ("main", "`python -m krisha.daily_report --scope rent`"),
    ],
    used_extra=[WF("rescrape-rent")],
    gotchas=["Strings from stats are HTML-escaped (parse_mode=HTML). Fail-soft: must not break the rescrape pipeline. The 14.07 breakage went unnoticed because the report needed mental comparison → verdict line first."],
)

FILES["src/krisha/monitoring.py"] = dict(
    tags=["layer/ops", "layer/ml"],
    concepts=["sale-price-model"],
    purpose="Model monitoring: metrics_history.jsonl append/load, dataset summary, Telegram retrain report.",
    symbols=[
        ("ADMIN_CHAT_ENV", "'TG_ADMIN_CHAT_ID' (imported by rescrape, report, daily_report)"),
        ("append_metrics_history", "one line per retrain"),
        ("format_retrain_report", "metrics + deltas vs gate baseline + verdict"),
        ("notify_retrain", "send via tg_call"),
    ],
    data=["models/metrics_history.jsonl, models/rent/metrics_history.jsonl."],
)

FILES["src/krisha/usage.py"] = dict(
    tags=["layer/ops"],
    concepts=["state-storage"],
    purpose="Usage stats (site visits, predictions, bot messages) in data/usage_stats.json, flushed to GitHub at most every 30 min; weekly admin report.",
    symbols=[
        ("FLUSH_INTERVAL", "30 min; KEEP_DAYS 60; ALMATY_TZ UTC+5"),
        ("_hash_user", "salted sha256(chat_id)[:10] (issue #116)"),
        ("record_event", "never raises; flush in daemon thread"),
        ("merge_states", "max counter wins"),
        ("send_weekly_report", "Mondays to TG_ADMIN_CHAT_ID"),
    ],
    data=["Env: STATE_ENCRYPTION_KEY or TELEGRAM_BOT_TOKEN (salt), TG_ADMIN_CHAT_ID, USAGE_FLUSH_SYNC."],
    gotchas=["Events since last flush are lost on restart (accepted)."],
)

FILES["src/krisha/prediction_log.py"] = dict(
    tags=["layer/ops", "layer/ml"],
    concepts=["state-storage"],
    purpose="Durable prediction log (issue #128) in data/prediction_log.json via save_json_state, because SQLite predictions table is wiped on each redeploy.",
    symbols=[
        ("LOG_PATH", "data/prediction_log.json, flat dict '<iso time>|<id>' → row"),
        ("MAX_ROWS", "20000"),
        ("_enabled", "only with PREDICTION_LOG=1; always off in GitHub Actions"),
        ("record", "never raises"),
    ],
    data=["Env PREDICTION_LOG, GITHUB_ACTIONS, USAGE_FLUSH_SYNC."],
    gotchas=["Each flush is a commit; enabling it without paths-ignore in deploy workflow would rebuild the Space every few minutes."],
)

# ----------------------------------------------------------------- API
FILES["src/krisha/api/app.py"] = dict(
    tags=["layer/api"],
    concepts=["web-api", "prod-runtime", "frontend"],
    purpose="FastAPI app: /api/predict, /api/health, stats/heatmap/forecast/demo/metrics, Telegram webhook, in-memory static site with live numbers, SEO endpoints, security headers, rate limiting, startup data/model download and cache warmup.",
    symbols=[
        ("_lifespan", "startup hook → _startup"),
        ("CSP", "Content-Security-Policy (extended by site_analytics when GA4 set)"),
        ("MAX_BODY_BYTES", "64 KiB request body limit (_ChunkedBodyLimitMiddleware, raw ASGI)"),
        ("DATA_STALE_AFTER_HOURS", "30 h freshness threshold"),
        ("health", "GET /api/health: model metrics, data freshness, tg webhook status, revision"),
        ("_client_ip", "rate-limit IP: X-Bagam-Client-IP if PROXY_KEY matches, else hops-th XFF from the right (TRUSTED_PROXY_HOPS=1)"),
        ("RATE_LIMIT_TOTAL", "env RATE_LIMIT_PER_WINDOW=15 per RATE_LIMIT_WINDOW_S=60, split per worker"),
        ("_rate_subject", "IPv4 address or IPv6 /64"),
        ("_PREDICT_LIMITER", "anyio CapacityLimiter(10) for heavy path"),
        ("predict", "POST /api/predict: cache peek BEFORE rate limit → gate in thread with PREDICT_WAIT_S=20 → error mapping 422/404/502/503"),
        ("demo", "GET /api/demo?deal=prodazha|arenda: random URL from cached pool of 100 fresh active lots (own rate bucket)"),
        ("stats", "GET /api/stats (SWR TTLCache 600 s)"),
        ("rent_stats", "GET /api/stats/rent"),
        ("heatmap", "GET /api/heatmap"),
        ("forecast", "GET /api/forecast (404 unless FEATURE_FORECAST)"),
        ("api_metrics", "GET /api/metrics per-worker counters"),
        ("telegram_webhook", "POST /tg/webhook: secret header check, update_id dedup (deque + DB), background processing (BOT_UPDATE_CONCURRENCY=4)"),
        ("_startup_lock", "fcntl flock so one worker prepares data (no-op on Windows)"),
        ("_prepare_data", "pull_state → ensure_db/models (+rent) → init_db"),
        ("_warmup_runtime_caches", "load models, spatial refs, POI index, stats/heatmap/demo caches"),
        ("_start_session_warmup", "warm krisha sticky session in background (KRISHA_SESSION_WARMUP=0 disables)"),
        ("_render_pages", "base HTML + live numbers, JSON-LD, counters → in-memory assets"),
        ("readyz", "model + DB on disk + pages in memory"),
        ("sitemap_xml", "/sitemap.xml"),
        ("_CachedStatic", "/static mount from precompressed memory"),
    ],
    data=[
        "Env: PROXY_KEY, TRUSTED_PROXY_HOPS, RATE_LIMIT_PER_WINDOW, RATE_LIMIT_WINDOW_S, DEMO_RATE_LIMIT_PER_WINDOW, WEB_CONCURRENCY, PREDICT_WAIT_S, HEALTH_CACHE_TTL_S, DEMO_POOL_TTL_S, BOT_UPDATE_CONCURRENCY, KRISHA_STATE_PULL, KRISHA_SESSION_WARMUP, KRISHA_LIVE_PAGES (=0 → no live numbers in HTML, tests/e2e), BUILD_REVISION, PUBLIC_BASE_URL, OMP_NUM_THREADS (+ site_analytics envs).",
        "Files: static/ (served from memory), data/*.db, models/*.",
        "Run: `uvicorn krisha.api.app:app` (Dockerfile, Makefile).",
    ],
    depends_extra=["[[concepts/frontend]] (static/ pages)"],
    gotchas=[
        "Exception order matters: ChallengeBlocked/ListingNotFound/SourceUnavailable are RuntimeError subclasses and ListingOutsideAlmaty a ValueError subclass — specific branches must stay above generic ones.",
        "Any other ValueError (e.g. JSONDecodeError on broken meta) → 502, not 422 with raw text.",
        "Leftmost XFF is client-controlled — never use it (prod bypass found on HF).",
        "Cache hits are served before rate limit on purpose (CGNAT crowds from one channel post).",
        "HF Space restarts after each rescrape/retrain, so live numbers in HTML are ≤1 day old; per-process metrics/caches with WEB_CONCURRENCY>1.",
        "Webhook setup runs in a thread: Telegram unreachability used to block port open ~100 s.",
    ],
)

FILES["src/krisha/api/cache.py"] = dict(
    tags=["layer/api"],
    concepts=["web-api"],
    purpose="Thread-safe TTL cache primitive with per-key single-flight, stale-on-error and stale-while-revalidate.",
    symbols=[
        ("TTLCache", "ttl, maxsize, stale_ttl, serve_stale_while_refreshing"),
        ("TTLCache.get_or_call", "single producer per key; others wait; optional recheck under key lock"),
        ("TTLCache.peek", "(age, value) without producing"),
    ],
    gotchas=["Per-process: with several workers each has its own cache."],
)

FILES["src/krisha/api/schemas.py"] = dict(
    tags=["layer/api"],
    concepts=["web-api"],
    purpose="Pydantic request/response models of the API.",
    symbols=[
        ("PredictRequest", "{url}"),
        ("Factor", "SHAP factor with impact and hint"),
        ("ScamRisk", "level, below_pct, reasons"),
        ("Analog", "similar listing"),
        ("Liquidity", "срок продажи"),
        ("RentalYield", "rent model on a sale lot"),
        ("PredictResponse", "full card contract incl. deal, price_period, room_share, fair_price(_low/_high), verdict, diff_pct, delisted_at, duplicate_of"),
        ("DemoResponse", "{url}"),
        ("HealthResponse", "status, model metrics, freshness, tg_webhook, revision, rent model fields"),
    ],
    gotchas=["Contract tests: [[code/tests/test_api_schema.py]]; e2e fixtures in tests/e2e/fixtures."],
)

FILES["src/krisha/api/metrics.py"] = dict(
    tags=["layer/api"],
    concepts=["web-api"],
    purpose="In-process hot-path counters and latency ring buffers (512 per route) for /api/metrics.",
    symbols=[("bump", "named counter"), ("observe", "route latency+status"), ("snapshot", "p50/p95/p99 + counters")],
    gotchas=["Per-process: /api/metrics shows only the worker that served the request; HF bans IP for load tests so this is the only peak visibility."],
)

FILES["src/krisha/api/static_cache.py"] = dict(
    tags=["layer/api"],
    concepts=["web-api", "frontend"],
    purpose="Static files read and gzipped once at startup and served from memory with ETag/304 and content-hash ?v= versioning.",
    symbols=[
        ("Asset", "raw bytes, gzip bytes, ETags"),
        ("build_site", "cache + versions + rewrite /static refs with ?v=<hash>"),
        ("versioned_refs", "`/static/x.css` → `?v=<10-char hash>`"),
        ("replace_origins", "hardcoded site origin in pages → target origin"),
        ("negotiate", "(status, body, headers) for request"),
    ],
    gotchas=["Measured: gzip per request halved homepage throughput (361 vs 706 rps)."],
)

FILES["src/krisha/api/live_pages.py"] = dict(
    tags=["layer/api"],
    concepts=["web-api", "frontend"],
    purpose="Server-side fill of [data-l=…] live numbers, district bars, FAQ/JSON-LD, rent variant of stats page, <noscript> market summary and /llms.txt — formats identical to static site.js.",
    symbols=[
        ("live_values", "texts for [data-l] keys from stats+health"),
        ("fill_live", "substitute values in HTML"),
        ("district_bars", "homepage bars markup like draw() in index.html"),
        ("faq_json_ld", "FAQPage from visible .qa blocks"),
        ("rent_variant", "stats.html for /stats?mode=rent with own title/canonical"),
        ("llms_txt", "/llms.txt text"),
    ],
    gotchas=["Formats must match site.js char-for-char (e.g. `_js_round` = JS Math.round) or numbers flicker after API response."],
)

FILES["src/krisha/api/site_analytics.py"] = dict(
    tags=["layer/api"],
    concepts=["web-api"],
    purpose="GA4 counter and search-engine verification codes from env; extends CSP only when configured.",
    symbols=[
        ("GA_ENV", "GA4_MEASUREMENT_ID (G-…)"),
        ("YANDEX_VERIFICATION_ENV", "YANDEX_VERIFICATION"),
        ("GOOGLE_VERIFICATION_ENV", "GOOGLE_SITE_VERIFICATION"),
        ("csp_sources", "extra CSP sources for GA"),
        ("head_snippet", "static/js/analytics.js tag with data-ga"),
    ],
    gotchas=["Yandex Metrica removed 06.10.2026 (GA4 only). Empty env = no counters and unchanged CSP."],
)
