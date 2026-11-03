---
type: file
path: tests/test_no_direct_sqlite_connect.py
tags: [zone/tests, layer/db]
updated: 2026-11-03
---
# tests/test_no_direct_sqlite_connect.py
purpose: source-scan regression guard (issue #115): no `sqlite3.connect(` in src/krisha outside db.py + allowlist.

## Key symbols
- `_PRE_EXISTING_ALLOWLIST = {db.py, stats.py, train.py, complexes.py}`
- `test_no_new_direct_sqlite_connect_outside_db`

## Depends on
- [[code/src/krisha/db.py]] (`get_conn` sets WAL + busy_timeout)

## Gotchas
- Origin: analogs/alerts/daily_report used raw connect on /api/predict hot path → `database is locked`.
- Do not extend allowlist without justification comment.
