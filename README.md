# 🏠 Krisha Fair Price — справедливая цена квартир в Алматы

ML-сервис, который по ссылке на объявление Krisha.kz говорит: **переплата, выгодно или справедливая цена** — и объясняет почему (SHAP).

Стек: Python 3.11 · httpx + BeautifulSoup4 · SQLite · pandas · CatBoost · SHAP · FastAPI · Tailwind · GitHub Actions.

## 🚀 Быстрый старт

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

make crawl        # быстрая проба: соберёт ~50 объявлений в data/krisha.db
make crawl-full   # полный сбор (~300 страниц, 1-2 часа из-за вежливых пауз)
make train        # обучить CatBoost → models/model.cbm + reports/shap_summary.png
make api          # http://localhost:8000 — фронт + API
make test         # pytest
```

## 📦 Что уже сделано (основа, ~80%)

| Модуль | Что делает |
|---|---|
| `src/krisha/scraping/client.py` | Вежливый HTTP-клиент: паузы 2–4 сек, ретраи, обработка 403/429 |
| `src/krisha/scraping/listing_parser.py` | Парсер страницы выдачи → id объявлений |
| `src/krisha/scraping/detail_parser.py` | Парсер объявления: `window.data` JSON + HTML-параметры (проверен на реальных страницах) |
| `src/krisha/scraping/crawler.py` | Возобновляемый краулер → SQLite (Ctrl+C безопасен) |
| `src/krisha/db.py` | Схема SQLite + upsert по id |
| `src/krisha/features.py` | Очистка мусора + фичи: этажность, возраст дома, расстояние до центра… |
| `src/krisha/train.py` | CatBoost на log(price), сравнение с baseline (медиана ₸/м² по району), SHAP-отчёт |
| `src/krisha/predict.py` | Предсказание по URL: вердикт GOOD_DEAL / FAIR / OVERPRICED + топ-факторы |
| `src/krisha/api/` | FastAPI: `POST /api/predict`, `GET /api/health` + статичный фронт |
| `static/index.html` | Фронт на Tailwind: вставил ссылку → получил вердикт |
| `tests/` | 15+ тестов: парсеры (с фикстурой реальной вёрстки), фичи, БД, смоук обучения |
| `.github/ci.yml.example` | CI (ruff + pytest): переименуй в `.github/workflows/ci.yml`, чтобы включить — см. комментарий в файле |

## 🤖 Что осталось (отдать Sonnet'у)

- [ ] Docker (Dockerfile + docker-compose) и деплой на Railway/Render
- [ ] Telegram-бот (aiogram): та же логика, что `/api/predict`
- [ ] EDA-ноутбук в `notebooks/` (распределения цен, карта, корреляции)
- [ ] Тюнинг гиперпараметров CatBoost (Optuna), больше фич из `raw_params` (ремонт, мебель, парковка)
- [ ] Бейджи CI/прочее в README, скриншоты, демо-ссылка

Детальные промпты для каждого шага — в [`PLAN/`](PLAN/README.md).

## 🧠 Как это работает

1. **Краулер** обходит выдачу `krisha.kz/prodazha/kvartiry/almaty/`, c каждой детальной страницы берёт встроенный JSON `window.data` (цена, площадь, комнаты, адрес, координаты) + HTML-параметры (этаж, год, тип дома, потолки) → SQLite.
2. **Модель** — CatBoostRegressor на `log1p(price)` с категориальными фичами (район, ЖК, тип дома). Метрики сравниваются с baseline «медианная цена за м² по (район × комнаты)» — модель обязана его бить.
3. **Сервис** по URL скачивает объявление, прогоняет через ту же модель и отвечает: справедливая цена, отклонение в %, вердикт и топ-факторы (SHAP).

## ⚠️ Дисклеймер

Данные принадлежат Krisha.kz. Проект учебный, парсинг — бережный (паузы 2–4 сек, ничего не перегружаем). Не используйте для коммерческих целей.
