---
type: file
path: src/krisha/scraping/complex_parser.py
tags: [zone/src, layer/scraping]
updated: 2026-11-03
---
# src/krisha/scraping/complex_parser.py
purpose: Parse a residential-complex (ЖК) page /complex/show/... into a row for table `complexes` (developer, housing class, completion year, floors…).
concepts: [[concepts/scraping-krisha]], [[concepts/feature-engineering]]
## Key symbols
- `parse_complex` — html → dict for db.COMPLEX_COLUMNS; sources: window.data.complex JSON, `<dt data-name>` params, sidebar `src/krisha/scraping/complex_parser.py:84`
- `PARAM_MAP` — krisha param name → column (housingClass→housing_class, count.of.floors→floors_raw, home.type→material…) `src/krisha/scraping/complex_parser.py:26`
- `parse_completion_year` — takes the LAST year mentioned in deadline text `src/krisha/scraping/complex_parser.py:66`
- `parse_max_floors` — «7-9 этажей» → 9 `src/krisha/scraping/complex_parser.py:74`
## Depends on
- [[code/src/krisha/scraping/json_window.py]]
## Used by
- [[code/scripts/crawl_complexes.py]]
- tests: [[code/tests/_dir|tests/test_complexes.py]]
