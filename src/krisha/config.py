"""Центральная конфигурация проекта. Всё, что можно подкрутить — здесь."""

import os as _os
from pathlib import Path

# --- Пути ---------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = ROOT_DIR / "models"
REPORTS_DIR = ROOT_DIR / "reports"
DB_PATH = DATA_DIR / "krisha.db"
MODEL_PATH = MODELS_DIR / "model.cbm"
# Квантильные модели для доверительного интервала цены (нижняя/верхняя границы)
MODEL_LO_PATH = MODELS_DIR / "model_lo.cbm"
MODEL_HI_PATH = MODELS_DIR / "model_hi.cbm"
MODEL_META_PATH = MODELS_DIR / "model_meta.json"
COMPLEXES_SNAPSHOT_PATH = MODELS_DIR / "complexes.json"
OSM_POIS_SNAPSHOT_PATH = MODELS_DIR / "osm_pois.json"

# --- Парсинг ------------------------------------------------------------
BASE_URL = "https://krisha.kz"
SEARCH_URL = f"{BASE_URL}/prodazha/kvartiry/almaty/"
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
REQUEST_TIMEOUT = float(_os.environ.get("KRISHA_TIMEOUT", "30"))
MAX_RETRIES = int(_os.environ.get("KRISHA_MAX_RETRIES", "3"))

# --- Модель -------------------------------------------------------------
# Центр Алматы (пересечение Абая/Достык, условно) — для фичи "расстояние до центра"
ALMATY_CENTER = (43.2398, 76.8898)

# Фильтры адекватности данных (квартиры в Алматы, продажа)
PRICE_MIN = 5_000_000        # ₸
PRICE_MAX = 1_500_000_000    # ₸
AREA_MIN = 10.0              # м²
AREA_MAX = 500.0             # м²
PPSM_MIN = 100_000           # ₸/м² — ниже почти наверняка мусор
PPSM_MAX = 5_000_000         # ₸/м²

RANDOM_STATE = 42
