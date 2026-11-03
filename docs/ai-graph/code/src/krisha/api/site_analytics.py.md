---
type: file
path: src/krisha/api/site_analytics.py
tags: [zone/src, layer/api]
updated: 2026-11-03
---
# src/krisha/api/site_analytics.py
purpose: GA4 counter and search-engine verification codes from env; extends CSP only when configured.
concepts: [[concepts/web-api]]
## Key symbols
- `GA_ENV` — GA4_MEASUREMENT_ID (G-…) `src/krisha/api/site_analytics.py:30`
- `YANDEX_VERIFICATION_ENV` — YANDEX_VERIFICATION `src/krisha/api/site_analytics.py:31`
- `GOOGLE_VERIFICATION_ENV` — GOOGLE_SITE_VERIFICATION `src/krisha/api/site_analytics.py:32`
- `csp_sources` — extra CSP sources for GA `src/krisha/api/site_analytics.py:79`
- `head_snippet` — static/js/analytics.js tag with data-ga `src/krisha/api/site_analytics.py:113`
## Used by
- [[code/src/krisha/api/app.py]]
- tests: [[code/tests/_dir|tests/test_page_privacy.py]], [[code/tests/_dir|tests/test_seo_live_pages.py]]
## Gotchas
- Yandex Metrica removed 06.10.2026 (GA4 only). Empty env = no counters and unchanged CSP.
