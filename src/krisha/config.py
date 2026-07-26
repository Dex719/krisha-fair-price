"""Центральная конфигурация проекта. Всё, что можно подкрутить — здесь."""

import os as _os
from pathlib import Path

# --- Пути ---------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = ROOT_DIR / "models"
REPORTS_DIR = ROOT_DIR / "reports"
DB_PATH = DATA_DIR / "krisha.db"
# Аренда: отдельная база с той же схемой — не трогает продажный пайплайн,
# все функции db.py принимают db_path (модель аренды и «купить vs снимать» — этап 2)
RENT_DB_PATH = DATA_DIR / "krisha_rent.db"
MODEL_PATH = MODELS_DIR / "model.cbm"
# issue #132: одна MultiQuantile-модель (q10+q90 разом) вместо двух
# отдельных Quantile-моделей — квантили гарантированно не пересекаются,
# обучение быстрее. train.py больше НЕ пишет MODEL_LO_PATH/MODEL_HI_PATH.
MODEL_QUANTILE_PATH = MODELS_DIR / "model_quantile.cbm"
# Легаси-пути (до issue #132) — держим только как источник для миграционного
# фолбэка в predict.load_interval_models(): model_quantile.cbm появится лишь
# после ближайшего retrain, а до этого прод должен продолжать отдавать
# интервал по уже опубликованным старым моделям, а не падать на плоский
# ±10%. Удалить вместе с фолбэком, когда retrain подтвердит новую модель.
MODEL_LO_PATH = MODELS_DIR / "model_lo.cbm"
MODEL_HI_PATH = MODELS_DIR / "model_hi.cbm"
MODEL_META_PATH = MODELS_DIR / "model_meta.json"
# issue #106: APE-пары (новая/старая модель на одном test-сплите) для
# парного бутстрепа в scripts/model_gate.py — пишется только когда есть
# честное сравнение (train() запущен с --compare-old и старая модель
# оценилась без ошибок).
MODEL_GATE_SAMPLES_PATH = MODELS_DIR / "model_gate_samples.json"
COMPLEXES_SNAPSHOT_PATH = MODELS_DIR / "complexes.json"
OSM_POIS_SNAPSHOT_PATH = MODELS_DIR / "osm_pois.json"
OSM_ZONES_SNAPSHOT_PATH = MODELS_DIR / "osm_zones.json"
SPATIAL_REF_PATH = MODELS_DIR / "spatial_ref.json"

# --- Парсинг ------------------------------------------------------------
BASE_URL = "https://krisha.kz"
SEARCH_URL = f"{BASE_URL}/prodazha/kvartiry/almaty/"

# --- Шардирование выдачи (этап 4: полное покрытие рескрейпом) -------------
# Общая выдача по Алматы показывает ~44к объявлений, но пагинация обрезается
# на 1000 страницах (~20к), а обход по 400 страниц покрывал лишь «популярные»
# ~7-8к. Дробим выдачу на шарды «район × комнаты» — каждый шард целиком
# влезает в свою пагинацию, суммарно покрываем почти весь город, и delisted
# становится честным.
ALMATY_DISTRICT_SLUGS = {
    "Алатауский": "almaty-alatauskij",
    "Алмалинский": "almaty-almalinskij",
    "Ауэзовский": "almaty-aujezovskij",
    "Бостандыкский": "almaty-bostandykskij",
    "Жетысуский": "almaty-zhetysuskij",
    "Медеуский": "almaty-medeuskij",
    "Наурызбайский": "almaty-nauryzbajskiy",  # именно -iy: слаг с -ij не существует
    "Турксибский": "almaty-turksibskij",
}
# Значения фильтра das[live.rooms][]: 1/2/3 отдельно, «4+» = 4 и 5 (5 = «5 и более»)
ROOM_SHARDS = {
    "1к": ("1",),
    "2к": ("2",),
    "3к": ("3",),
    "4к+": ("4", "5"),
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Бережный режим: пауза между запросами (секунды, случайная в диапазоне).
# Можно переопределить через env: KRISHA_DELAY_MIN / KRISHA_DELAY_MAX.
REQUEST_DELAY_RANGE = (
    float(_os.environ.get("KRISHA_DELAY_MIN", "2.0")),
    float(_os.environ.get("KRISHA_DELAY_MAX", "4.0")),
)
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3

# --- Модель -------------------------------------------------------------
# Центр Алматы (пересечение Абая/Достык, условно) — для фичи "расстояние до центра"
ALMATY_CENTER = (43.2398, 76.8898)

# Фильтры адекватности данных (квартиры в Алматы, продажа)
PRICE_MIN = 5_000_000        # ₸
PRICE_MAX = 1_500_000_000    # ₸
# Аренда: цена — ₸/месяц, порядок величины другой. Проверять арендную цену
# продажным контрактом нельзя: PRICE_MIN=5 млн отбраковывает вообще любую
# аренду, из-за чего цены в krisha_rent.db переставали обновляться совсем.
RENT_PRICE_MIN = 20_000      # ₸/мес
RENT_PRICE_MAX = 10_000_000  # ₸/мес
AREA_MIN = 10.0              # м²
AREA_MAX = 500.0             # м²
PPSM_MIN = 100_000           # ₸/м² — ниже почти наверняка мусор
PPSM_MAX = 5_000_000         # ₸/м²

# issue #104/#108: грубый bbox Алматы (город + пригороды с запасом) — координаты
# за его пределами почти всегда чужой город (Астана, Шымкент...) в базе из-за
# битого парсинга/ручного ввода, а не реальный Алматы.
ALMATY_BBOX = {
    "lat_min": 42.95,
    "lat_max": 43.50,
    "lon_min": 76.55,
    "lon_max": 77.25,
}

# issue #103: сколько объявлений должно сидеть на одной (округлённой) точке,
# чтобы считать её меткой ЖК, а не координатой конкретной квартиры. Раньше
# жило только в krisha.zones (тянет numpy/pandas) — вынесено сюда, чтобы
# db.py (лёгкий, на hot path API) мог посчитать coords_approx при upsert без
# тяжёлых зависимостей; krisha.zones импортирует значение отсюда же.
SHARED_PIN_MIN = 5

# issue #104: сколько дней после delisted_at ещё доверяем последней цене в
# train (лот снят с продажи недавно — цена ещё рыночная; снят месяцы назад —
# устарела, но is_active было бы слишком строго и выкинуло бы половину истории).
STALE_DELISTED_DAYS = 90

RANDOM_STATE = 42
