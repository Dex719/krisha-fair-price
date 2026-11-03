---
type: file
path: src/krisha/scraping/sweep_state_file.py
tags: [zone/src, layer/scraping, layer/ops]
updated: 2026-11-03
---
# src/krisha/scraping/sweep_state_file.py
purpose: Persist sweep_state keys `pass_seq:<deal>` and `ban_streak:<deal>` outside SQLite as JSON asset `sweep_state_<deal>.json` in release db-latest, so failed passes still advance them (audit 24.10).
concepts: [[concepts/scraping-krisha]]
## Key symbols
- `KEYS` — ('pass_seq', 'ban_streak') `src/krisha/scraping/sweep_state_file.py:42`
- `export_state` — DB → payload `src/krisha/scraping/sweep_state_file.py:60`
- `import_state` — merge into DB: pass_seq = max(db, file); ban_streak = value with later updated_at `src/krisha/scraping/sweep_state_file.py:67`
- `pull` — download asset from db-latest via db_release and import; fail-soft `src/krisha/scraping/sweep_state_file.py:104`
- `main` — CLI `python -m krisha.scraping.sweep_state_file {export --out|import --file|pull} [--deal] [--db]` `src/krisha/scraping/sweep_state_file.py:131`
## Depends on
- [[code/src/krisha/config.py]]
- [[code/src/krisha/db.py]]
- [[code/src/krisha/db_release.py]]
## Used by
- [[ops/workflows/rescrape]]
- [[ops/workflows/rescrape-rent]]
- tests: [[code/tests/_dir|tests/test_sweep_state_file.py]]
## Gotchas
- DB is uploaded to the release only after a successful pass; without this file a banned (failed) pass never incremented ban_streak, so the ban rollback never fired.
