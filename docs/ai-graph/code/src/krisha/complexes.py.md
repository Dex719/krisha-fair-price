---
type: file
path: src/krisha/complexes.py
tags: [zone/src, layer/ml]
updated: 2026-11-03
---
# src/krisha/complexes.py
purpose: ЖК (residential complex) reference: name normalization, snapshot to models/complexes.json for DB-less deploy, lookup of complex attributes for features.
concepts: [[concepts/feature-engineering]]
## Key symbols
- `normalize_complex_name` — «ЖК "Хан-Тенгри"» → «хан тенгри» (strip prefixes ЖК/жилой комплекс/кг/мкр/клубный дом, quotes, separators) `src/krisha/complexes.py:36`
- `COMPLEX_ATTRS` — developer, housing_class, completion_year, construction_status, material, max_floors, apartments_count `src/krisha/complexes.py:30`
- `lookup_complex_attrs` — exact match, else retry without phase/queue suffix `src/krisha/complexes.py:49`
- `snapshot_complexes` — table complexes → models/complexes.json {name_norm: attrs} `src/krisha/complexes.py:63`
- `load_complex_lookup` — snapshot → dict; missing file → {} (features become 'unknown') `src/krisha/complexes.py:95`
## Depends on
- [[code/src/krisha/config.py]]
## Used by
- [[code/src/krisha/features.py]]
- [[code/src/krisha/predict.py]]
- [[code/scripts/crawl_complexes.py]]
- tests: [[code/tests/_dir|tests/test_complexes.py]]
## Data / env
- Reads table `complexes` in data/krisha.db (filled by [[code/scripts/crawl_complexes.py]]); writes/reads models/complexes.json (shipped in model-latest).
## Gotchas
- Join key on listing side: raw_params['map.complex'] (~58% filled) else complex_name.
