.PHONY: install crawl crawl-full train api test lint

install:        ## зависимости (venv создай заранее: python -m venv .venv && source .venv/bin/activate)
	pip install -e ".[dev]"

crawl:          ## быстрая проба: 5 страниц, до 50 объявлений
	python scripts/crawl.py --pages 5 --limit 50

crawl-full:     ## полный сбор: ~300 страниц
	python scripts/crawl.py --pages 300

train:          ## обучить модель
	python scripts/train.py

api:            ## запустить API + фронт на http://localhost:8000
	uvicorn krisha.api.app:app --reload --port 8000

test:
	pytest -q

lint:
	ruff check src tests scripts
