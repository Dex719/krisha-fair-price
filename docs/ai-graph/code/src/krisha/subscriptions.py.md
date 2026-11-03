---
type: file
path: src/krisha/subscriptions.py
tags: [zone/src, layer/bot, layer/ops]
updated: 2026-11-03
---
# src/krisha/subscriptions.py
purpose: Alert subscriptions + generic JSON state persistence: local file + commit via GitHub Contents API to private STATE_REPO, Fernet encryption, merge-on-write, pull at startup.
concepts: [[concepts/telegram-bot]], [[concepts/state-storage]]
## Key symbols
- `STATE_REPO` — env KRISHA_STATE_REPO, default Dex719/krisha-db `src/krisha/subscriptions.py:58`
- `STATE_FILES` — subscriptions, tracked, usage_stats, prediction_log, channel_posted, alerted_ids, alerted_chats (.json) `src/krisha/subscriptions.py:62`
- `STATE_LOCK` — process RLock for state edits `src/krisha/subscriptions.py:55`
- `_fernet` — key from STATE_ENCRYPTION_KEY else TELEGRAM_BOT_TOKEN; none → plaintext (local dev) `src/krisha/subscriptions.py:74`
- `load_json_state` — decrypts {'_encrypted': …} wrapper or plain JSON `src/krisha/subscriptions.py:111`
- `parse_filters` — /alerts_on tail: 1–6 rooms, ≥7 = max price in mln ₸, word = district `src/krisha/subscriptions.py:144`
- `save_json_state` — write local atomically + push to GitHub (merge with remote first) `src/krisha/subscriptions.py:242`
- `_merge_remote` — top-level key union with deleted/touched keys, update_only (issue #111) `src/krisha/subscriptions.py:406`
- `_push_to_github` — PUT contents with retries (PUSH_MAX_ATTEMPTS=3), admin alert on failure `src/krisha/subscriptions.py:471`
- `pull_state` — download STATE_FILES into data/ at startup / before alerts `src/krisha/subscriptions.py:326`
- `main` — CLI `python -m krisha.subscriptions --pull` `src/krisha/subscriptions.py:620`
## Depends on
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/config.py]]
- [[code/src/krisha/stats.py]]
- [[code/src/krisha/tracking.py]]
## Used by
- [[code/src/krisha/alerts.py]]
- [[code/src/krisha/api/app.py]]
- [[code/src/krisha/bot.py]]
- [[code/src/krisha/channel.py]]
- [[code/src/krisha/prediction_log.py]]
- [[code/src/krisha/tracking.py]]
- [[code/src/krisha/usage.py]]
- [[code/scripts/send_alerts.py]]
- [[ops/workflows/rescrape]]
- [[ops/workflows/rescrape-rent]]
- tests: [[code/tests/conftest.py]], [[code/tests/_dir|tests/test_alerts.py]], [[code/tests/_dir|tests/test_bot.py]], [[code/tests/_dir|tests/test_bot_state_merge.py]], [[code/tests/_dir|tests/test_page_bot.py]], [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_prediction_log.py]], [[code/tests/_dir|tests/test_state_encryption.py]], [[code/tests/_dir|tests/test_state_key_workflows.py]], [[code/tests/_dir|tests/test_state_repo.py]], [[code/tests/_dir|tests/test_tracking.py]]
## Data / env
- Files data/*.json (STATE_FILES). Env: KRISHA_DB_TOKEN (preferred; legacy GITHUB_PAT/GITHUB_TOKEN), KRISHA_STATE_REPO, STATE_ENCRYPTION_KEY, TELEGRAM_BOT_TOKEN, STATE_FORCE_OVERWRITE, TG_ADMIN_CHAT_ID. Host api.github.com.
## Gotchas
- Hosting disk is wiped on restart: without token state lives until next restart.
- chat_id is PII → whole file encrypted.
- Writes merge only own touched keys over server copy, so a stale Actions copy can't roll back bot edits (fix 5e3c58f).
- FAIL-CLOSED (issue #150): if the remote file exists but cannot be read/decrypted (key changed, broken JSON) the push is cancelled instead of overwriting all subscribers; emergency override env STATE_FORCE_OVERWRITE=1.
