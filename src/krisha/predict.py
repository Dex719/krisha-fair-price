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
    df = listing_to_frame(listing)
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
