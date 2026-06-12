"""Предсказание справедливой цены: по URL объявления или по готовому dict.

Вердикты: GOOD_DEAL (дешевле модели на >10%), OVERPRICED (дороже на >10%),
FAIR — в пределах ±10%.
"""

import json
import logging
import re
from functools import lru_cache
from typing import Any

import numpy as np
from catboost import CatBoostRegressor, Pool

from krisha.config import MODEL_META_PATH, MODEL_PATH
from krisha.features import listing_to_frame
from krisha.scraping.client import PoliteClient
from krisha.scraping.detail_parser import parse_detail

logger = logging.getLogger(__name__)

KRISHA_URL_RE = re.compile(r"krisha\.kz/a/show/(\d+)")
VERDICT_THRESHOLD = 0.10  # ±10% — справедливая цена

USER_TYPE_RU = {
    "owner": "Собственник",
    "agent": "Специалист",
    "company": "Компания",
    "builder": "Застройщик",
}

# (подпись, ключ в raw_params) — extras со страницы объявления
EXTRA_PARAMS_RU = [
    ("Ремонт", "flat.renovation"),
    ("Санузел", "flat.toilet"),
    ("Балкон", "flat.balcony"),
    ("Парковка", "flat.parking"),
    ("Мебель", "live.furniture"),
    ("Безопасность", "flat.security"),
]


def build_details(listing: dict[str, Any]) -> list[dict[str, str]]:
    """Характеристики объявления для карточки на фронте: [{label, value}, ...]."""
    from krisha.stats import DISTRICT_RU  # здесь, чтобы не плодить циклы импортов

    try:
        raw = json.loads(listing.get("raw_params") or "{}")
    except json.JSONDecodeError:
        raw = {}

    floor, total = listing.get("floor"), listing.get("total_floors")
    area = listing.get("area")
    ceiling = listing.get("ceiling")
    district = listing.get("district")
    category = listing.get("category")

    items: list[tuple[str, Any]] = [
        ("Комнаты", listing.get("rooms")),
        ("Площадь", f"{area:g} м²" if area else None),
        ("Этаж", f"{floor} из {total}" if floor and total else floor),
        ("Год постройки", listing.get("year_built")),
        ("Тип дома", listing.get("building_type")),
        ("Потолки", f"{ceiling:g} м" if ceiling else None),
        ("Район", DISTRICT_RU.get(district, district) if district else None),
        ("Микрорайон", listing.get("microdistrict")),
        ("Жилой комплекс", listing.get("complex_name")),
        *((label, raw.get(key)) for label, key in EXTRA_PARAMS_RU),
        ("Продавец", USER_TYPE_RU.get(listing.get("user_type") or "")),
        ("Категория", "Новостройка" if category == "novostroiki" else "Вторичка" if category else None),
    ]
    return [{"label": label, "value": str(value)} for label, value in items if value not in (None, "")]


# (подпись, ключ в справочнике ЖК) — блок «О доме»
COMPLEX_PARAMS_RU = [
    ("Застройщик", "developer"),
    ("Класс жилья", "housing_class"),
    ("Год сдачи", "completion_year"),
    ("Статус", "construction_status"),
    ("Материал", "material"),
    ("Этажность", "max_floors"),
    ("Квартир в ЖК", "apartments_count"),
]


def build_complex_details(listing: dict[str, Any]) -> list[dict[str, str]]:
    """Блок «О доме» из справочника ЖК (этап 2). Нет ЖК в базе → пустой список."""
    from krisha.complexes import load_complex_lookup, lookup_complex_attrs

    try:
        raw = json.loads(listing.get("raw_params") or "{}")
    except json.JSONDecodeError:
        raw = {}
    name = raw.get("map.complex") or listing.get("complex_name")
    attrs = lookup_complex_attrs(name, load_complex_lookup())
    if not attrs:
        return []
    return [
        {"label": label, "value": str(attrs[key])}
        for label, key in COMPLEX_PARAMS_RU
        if attrs.get(key) not in (None, "")
    ]


@lru_cache(maxsize=1)
def load_model() -> tuple[CatBoostRegressor, dict]:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Модель не найдена: {MODEL_PATH}. Сначала обучи: python scripts/train.py"
        )
    model = CatBoostRegressor()
    model.load_model(str(MODEL_PATH))
    meta = json.loads(MODEL_META_PATH.read_text())
    return model, meta


def _verdict(actual: float, fair: float) -> str:
    diff = (actual - fair) / fair
    if diff <= -VERDICT_THRESHOLD:
        return "GOOD_DEAL"
    if diff >= VERDICT_THRESHOLD:
        return "OVERPRICED"
    return "FAIR"


def top_factors(model: CatBoostRegressor, pool: Pool, features: list[str], n: int = 5) -> list[dict]:
    """Топ-факторы цены для конкретного объявления через SHAP-значения CatBoost."""
    shap_vals = model.get_feature_importance(pool, type="ShapValues")[0][:-1]
    order = np.argsort(np.abs(shap_vals))[::-1][:n]
    return [
        {"feature": features[i], "impact": float(shap_vals[i])}
        for i in order
        if abs(shap_vals[i]) > 1e-9
    ]


def predict_from_listing(listing: dict[str, Any]) -> dict[str, Any]:
    model, meta = load_model()
    features = meta["features"]
    df = listing_to_frame(listing, ppsm_maps=meta.get("ppsm_maps"))
    pool = Pool(df[features], cat_features=meta["cat_features"])
    fair_price = float(np.expm1(model.predict(pool)[0]))

    actual = listing.get("price")
    result = {
        "listing_id": listing.get("id"),
        "url": listing.get("url"),
        "title": listing.get("title"),
        "address": listing.get("address_title"),
        "actual_price": actual,
        "fair_price": round(fair_price, -4),  # округляем до 10 тыс ₸
        "verdict": _verdict(actual, fair_price) if actual else None,
        "diff_pct": round((actual - fair_price) / fair_price * 100, 1) if actual else None,
        "top_factors": top_factors(model, pool, features),
        "details": build_details(listing),
        "complex_details": build_complex_details(listing),
        "photos": (listing.get("photos") or [])[:12],
        "description": (listing.get("description") or None),
    }
    return result


def predict_from_url(url: str) -> dict[str, Any]:
    if not KRISHA_URL_RE.search(url):
        raise ValueError("Ожидается ссылка вида https://krisha.kz/a/show/<id>")
    with PoliteClient(delay_range=(0.5, 1.0)) as client:
        html = client.get(url)
    if html is None:
        raise RuntimeError("Не удалось загрузить объявление")
    listing = parse_detail(html, url)
    if listing is None:
        raise RuntimeError("Не удалось распарсить объявление")
    return predict_from_listing(listing)
