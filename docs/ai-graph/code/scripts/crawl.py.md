---
type: file
path: scripts/crawl.py
tags: [zone/scripts, layer/scraping]
updated: 2026-11-03
---
# scripts/crawl.py
purpose: original one-shot crawler CLI (krisha.kz search → SQLite). Superseded in production by [[code/scripts/rescrape.py]]. See [[concepts/scraping-krisha]].
## CLI
- `--pages 50` (max search pages), `--limit N` (max new listings), `--no-skip` (re-parse known ids)
## Depends on
- [[code/src/krisha/scraping/crawler.py]] (`crawl`)
## Used by
- `Makefile` targets: `--pages 5 --limit 50` (quick probe), `--pages 300` (full, ~6000 listings). No workflow.
