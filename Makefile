.PHONY: install crawl crawl-full crawl-complexes rescrape train api test test-e2e lint lock

install:        ## зависимости (venv создай заранее: python -m venv .venv && source .venv/bin/activate)
	pip install -e ".[dev]"

# Train-lock компилируется С ОГРАНИЧЕНИЕМ -c requirements.lock: раньше оба
# резолвились независимо, и общие пакеты (certifi, pillow…) расходились по
# версиям. Платформа — linux: lock ставят Space и раннеры Actions; без неё
# uv на Windows дописывает colorama/tzdata. Обновить один пакет:
#   make lock UV_FLAGS="--upgrade-package pillow"
LOCK_FLAGS = --python-version 3.11 --python-platform x86_64-unknown-linux-gnu $(UV_FLAGS)

lock:           ## перегенерировать requirements.lock / requirements-train.lock / requirements-runtime.lock
	uv pip compile pyproject.toml -o requirements.lock $(LOCK_FLAGS)
	uv pip compile pyproject.toml --extra train -c requirements.lock -o requirements-train.lock $(LOCK_FLAGS)
	python scripts/gen_runtime_lock.py   # requirements.lock минус plotting-хвост catboost (issue #119)

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

test-e2e:       ## браузерные e2e: заранее `playwright install chromium`
	pytest -m e2e -q

lint:
	ruff check src tests scripts
