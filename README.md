---
title: FairPrice
emoji: 🏠
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.png">
  <img src="docs/logo-light.png" alt="FairPrice" width="440" />
</picture>

**FairPrice — справедливая цена квартиры в Алматы: вставь ссылку на объявление и узнай, выгодно это, в рынке или переплата**

[![CI](https://github.com/Dex719/krisha-fair-price/actions/workflows/ci.yml/badge.svg)](https://github.com/Dex719/krisha-fair-price/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-174_passed-2ea44f)
![license](https://img.shields.io/badge/license-MIT-green)
![python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![CatBoost](https://img.shields.io/badge/CatBoost-MAPE_9.5%25-FFCC00)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![version](https://img.shields.io/badge/version-0.2.0-blue)

**[🚀 Сайт](https://dex719-krisha-fair-price.hf.space)** · **[📊 Рынок Алматы](https://dex719-krisha-fair-price.hf.space/stats)** · **[🤖 Telegram-бот](https://t.me/fairprice_kzbot)** · **[🧵 Threads](https://www.threads.com/@fairprice_kz)**

<img src="docs/screenshot-light.png" alt="Вердикт по объявлению" width="800" />

</div>

---

## ✨ Что умеет

1. Вставляешь ссылку на объявление о продаже квартиры в Алматы.
2. Сервис скачивает объявление и извлекает ~20 признаков (площадь, комнаты, этаж, возраст дома, район, ЖК, координаты, расстояние до центра…).
3. CatBoost-модель считает *справедливую* рыночную цену и сравнивает с ценой в объявлении.
4. Ответ: вердикт (`ВЫГОДНО` / `В РЫНКЕ` / `ПЕРЕПЛАТА`), отклонение в %, интервал цены и главные факторы, которые тянут цену вверх или вниз (SHAP).
5. Бонусы: медианный срок до снятия похожих объявлений («похожие уходят за ~N дней»), слежка за ценой (`/track` в боте — алерты при изменении цены и снятии), живой дашборд рынка Алматы.

## 🤖 Telegram-бот и Mini App

Тот же сервис доступен как **[@fairprice_kzbot](https://t.me/fairprice_kzbot)**: кидаешь ссылку — получаешь вердикт прямо в чате (фото, справедливая цена, факторы), плюс Mini App с полноценным интерфейсом сайта.

Бот работает на том же FastAPI через webhook (`POST /tg/webhook`) — отдельный сервер не нужен. Для своего деплоя достаточно задать `TELEGRAM_BOT_TOKEN`; webhook регистрируется при старте автоматически, а health-пинг раз в час проверяет его и чинит, если слетел.

## 📈 Модель

Обучена на **7 000+ реальных объявлениях** со всех 8 районов Алматы (вежливый resumable-краулер → SQLite). База пополняется ежедневным рескрейпом: свежие цены, новые объявления, отметки о снятии с продажи. Параллельно копится отдельная база аренды — под будущую модель арендной цены и калькулятор «купить vs снимать».

| Метрика | CatBoost | Бейзлайн (медиана ₸/м² по району × комнатам) |
|---|---|---|
| MAE | **7.4 млн ₸** | 14.2 млн ₸ |
| MAPE | **9.5%** | 18.0% |
| R² | **0.77** | 0.56 |

Метрики на **честном сплите**: перевыставленные дубли схлопнуты, train/test разбит по зданиям (`GroupShuffleSplit`) — модель не видит квартиры из тестового дома при обучении.

- Таргет: `log1p(price)`, нативные категориальные признаки (район, микрорайон, ЖК, тип дома).
- Пространственные признаки: медиана ₸/м² по гексагонам H3 (res 7 и 8), считается только на train и снапшотится в `models/spatial_ref.json`.
- Интервал цены: конформная калибровка (CQR), покрытие ~80%.
- Объяснимость: SHAP-факторы на каждый прогноз + глобальный отчёт в [`reports/shap_summary.png`](reports/shap_summary.png).
- Еженедельное переобучение с гейтом качества: новая модель заезжает, только если не хуже старой на свежем тесте.

## 🛠 Стек

`Python 3.11` · `httpx` + `BeautifulSoup4` · `SQLite` · `pandas` · `CatBoost` · `SHAP` · `FastAPI` · `vanilla JS` · `Material 3 Expressive` (светлая/тёмная) · `Chart.js` · `Leaflet` · `pytest` + `GitHub Actions` · `Hugging Face Spaces`

## 🚀 Быстрый старт

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"   # dev включает train-extras (shap, matplotlib)

make crawl        # быстрая проба: ~50 объявлений в data/krisha.db
make crawl-full   # полный обход Алматы (часы — вежливые задержки)
make train        # обучение CatBoost → models/model.cbm + SHAP-отчёт
make api          # http://localhost:8000 — веб-интерфейс + API
make test         # pytest
```

Runtime-деплой (Docker/HF Spaces) ставит только `pip install -e .` — тяжёлые train-зависимости (`shap`, `matplotlib`, `scikit-learn`) вынесены в optional extra `train` и нужны лишь для `make train` и SHAP-отчётов: `pip install -e ".[train]"`.

Версии зависимостей зафиксированы в `requirements.lock` (рантайм) и `requirements-train.lock` (обучение) — Docker ставит из `requirements.lock`, чтобы prod и Actions не разъезжались на разных версиях `catboost`/`pandas`/etc. После правки зависимостей в `pyproject.toml` перегенерируй оба лока и закоммить их в том же PR:

```bash
make lock
```

Сбор аренды (отдельная база `data/krisha_rent.db`):

```bash
python scripts/rescrape.py --deal arenda --max-new 1000
```

## 🔌 API

| Метод | Путь | Что делает |
|---|---|---|
| `POST` | `/api/predict` | `{"url": "https://krisha.kz/a/show/..."}` → вердикт, справедливая цена, факторы, срок продажи |
| `GET` | `/api/stats` | срез рынка: районы, ₸/м², распределение цен, карта |
| `GET` | `/api/health` | живость + статус модели и Telegram-webhook |
| `POST` | `/tg/webhook` | апдейты Telegram-бота (защищено секретом) |

## 🗂 Структура проекта

```
src/krisha/
├── scraping/       # вежливый HTTP-клиент (задержки, ретраи), парсеры выдачи и деталей, resumable-краулер, рескрейп
├── features.py     # очистка + фичи (этажность, возраст дома, до центра, карты ₸/м²)
├── train.py        # обучение CatBoost, бейзлайн, честная валидация, SHAP
├── predict.py      # ссылка → вердикт
├── market.py       # рыночная статистика: срез по районам, срок до снятия
├── bot.py          # Telegram-бот: webhook → predict → ответ; /track-алерты
├── api/            # FastAPI + статичный фронт
└── db.py           # SQLite: схема, upsert по id, история цен
static/             # Material 3 Expressive UI: оценка + дашборд рынка
tests/              # 174 теста: парсеры (фикстуры с реальной разметкой), фичи, БД, бот, API, train-smoke
```

## 🏷 Версии

Релизы нумеруются по [semver](https://semver.org) `0.MINOR.PATCH`, история — в [CHANGELOG.md](CHANGELOG.md). Текущая версия — в подвале сайта и в `/api/health`.

## ⚠️ Дисклеймер

Учебный проект. Данные объявлений принадлежат krisha.kz; сбор нарочно щадящий (случайные задержки, без параллельной долбёжки). Прогнозы — оценка, а не финансовый совет.

<div align="center">

<img src="docs/screenshot-dark.png" alt="Дашборд рынка Алматы, тёмная тема" width="800" />

</div>
