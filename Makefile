.PHONY: install crawl crawl-full crawl-complexes rescrape train api test lint

install:        ## зависимости (venv создай заранее: python -m venv .venv && source .venv/bin/activate)
	pip install -e ".[dev]"

crawl:          ## быстрая проба: 5 страниц, до 50 объявлений
	python scripts/crawl.py --pages 5 --limit 50

crawl-full:     ## полный сбор: ~300 страниц
	python scripts/crawl.py --pages 300

crawl-complexes: ## разовый скрейп каталога ЖК Алматы (этап 2)
	python scripts/crawl_complexes.py --skip-known

rescrape:       ## этап 4: регулярный проход — история цен, дни на рынке
	python scripts/rescrape.py

train:          ## обучить модель
	python scripts/train.py

api:            ## запустить API + фронт на http://localhost:8000
	uvicorn krisha.api.app:app --reload --port 8000

test:
	pytest -q

lint:
	ruff check src tests scripts
