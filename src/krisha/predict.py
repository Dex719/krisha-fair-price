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

from krisha.config import MODEL_HI_PATH, MODEL_LO_PATH, MODEL_META_PATH, MODEL_PATH
from krisha.features import listing_to_frame
from krisha.geo import build_location_details
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


@lru_cache(maxsize=1)
def load_interval_models() -> tuple[CatBoostRegressor, CatBoostRegressor] | None:
    """Квантильные модели нижней/верхней границы цены. None — если их нет
    (старая модель без интервала): тогда вердикт падает на плоский ±10%."""
    if not (MODEL_LO_PATH.exists() and MODEL_HI_PATH.exists()):
        return None
    lo = CatBoostRegressor()
    lo.load_model(str(MODEL_LO_PATH))
    hi = CatBoostRegressor()
    hi.load_model(str(MODEL_HI_PATH))
    return lo, hi


def _verdict(actual: float, fair: float) -> str:
    """Легаси-вердикт по плоскому порогу ±10% (когда нет интервала)."""
    diff = (actual - fair) / fair
    if diff <= -VERDICT_THRESHOLD:
        return "GOOD_DEAL"
    if diff >= VERDICT_THRESHOLD:
        return "OVERPRICED"
    return "FAIR"


def _verdict_interval(actual: float, low: float, high: float) -> str:
    """Вердикт по доверительному интервалу: «выгодно/дорого» — только когда
    цена объявления выходит ЗА интервал. Внутри — FAIR («в пределах рынка»).
    Это убирает «вердикт-в-шуме»: ярлык даём лишь при сигнале сильнее погрешности."""
    if actual < low:
        return "GOOD_DEAL"
    if actual > high:
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


def _with_money_impact(factors: list[dict], fair_price: float) -> list[dict]:
    """Переводит SHAP-вклад из log-пространства в понятные % и тенге.

    Модель предсказывает log1p(price), поэтому вклад s фактора — это
    множитель exp(s) к цене: impact_pct = (exp(s) - 1) * 100. В деньгах
    оцениваем «сколько фактор добавил к итоговой цене»: цена без него
    была бы fair/exp(s), значит вклад ≈ fair * (1 - exp(-s)).
    """
    for f in factors:
        s = f["impact"]
        f["impact_pct"] = round(float(np.expm1(s)) * 100, 1)
        f["impact_tenge"] = round(float(fair_price * (1 - np.exp(-s))), -4)
    return factors


def _with_hints(listing: dict[str, Any], factors: list[dict]) -> list[dict]:
    """Подсказки со статистикой рынка к каждому фактору (fail-soft)."""
    try:
        from krisha.factor_hints import build_factor_hints

        return build_factor_hints(listing, factors)
    except Exception:  # noqa: BLE001
        logger.exception("factor hints failed")
        return factors


def _location_details_with_pin_note(listing: dict[str, Any]) -> list[dict[str, str]]:
    """Блок «Локация» + бейдж «координаты примерные», если точка — метка ЖК."""
    from krisha.zones import approximate_pin_note

    lat, lon = listing.get("lat"), listing.get("lon")
    items = build_location_details(lat, lon)
    note = approximate_pin_note(lat, lon)
    if note is not None:
        items.append(note)
    return items


def predict_from_listing(
    listing: dict[str, Any], flags_live: bool = True
) -> dict[str, Any]:
    model, meta = load_model()
    features = meta["features"]

    # LLM-флаги достаём ДО фичей: они теперь и фичи модели, и бейджи карточки
    from krisha.llm_flags import flags_to_badges, get_flags_raw
    from krisha.spatial import load_spatial_ref

    raw_flags = get_flags_raw(listing, live=flags_live)
    listing = {**listing, "llm_flags": raw_flags if raw_flags is not None else None}

    df = listing_to_frame(
        listing, ppsm_maps=meta.get("ppsm_maps"), spatial_ref=load_spatial_ref()
    )
    # Район/микрорайон, восстановленные по OSM-зонам, показываем и в карточке
    from krisha.features import MISSING_CAT

    for col in ("district", "microdistrict"):
        val = df[col].iloc[0]
        if not listing.get(col) and isinstance(val, str) and val != MISSING_CAT:
            listing = {**listing, col: val}
    pool = Pool(df[features], cat_features=meta["cat_features"])
    fair_price = float(np.expm1(model.predict(pool)[0]))

    # Доверительный интервал: квантильные модели + CQR-сдвиг из меты.
    fair_low = fair_high = None
    interval = load_interval_models()
    if interval is not None:
        lo_model, hi_model = interval
        offset = float(meta.get("metrics", {}).get("interval", {}).get("cqr_offset_log", 0.0))
        fair_low = float(np.expm1(lo_model.predict(pool)[0] - offset))
        fair_high = float(np.expm1(hi_model.predict(pool)[0] + offset))
        if fair_low > fair_high:  # числовая страховка
            fair_low, fair_high = fair_high, fair_low
        # точка-оценка всегда внутри интервала (только расширяем, не сужаем)
        fair_low = min(fair_low, fair_price)
        fair_high = max(fair_high, fair_price)

    actual = listing.get("price")
    if actual and interval is not None:
        verdict = _verdict_interval(actual, fair_low, fair_high)
    elif actual:
        verdict = _verdict(actual, fair_price)
    else:
        verdict = None
    result = {
        "listing_id": listing.get("id"),
        "url": listing.get("url"),
        "title": listing.get("title"),
        "address": listing.get("address_title"),
        "actual_price": actual,
        "fair_price": round(fair_price, -4),  # округляем до 10 тыс ₸
        "fair_price_low": round(fair_low, -4) if fair_low is not None else None,
        "fair_price_high": round(fair_high, -4) if fair_high is not None else None,
        "verdict": verdict,
        "diff_pct": round((actual - fair_price) / fair_price * 100, 1) if actual else None,
        "top_factors": _with_hints(
            listing, _with_money_impact(top_factors(model, pool, features), fair_price)
        ),
        "details": build_details(listing),
        "complex_details": build_complex_details(listing),
        "location_details": _location_details_with_pin_note(listing),
        "photos": (listing.get("photos") or [])[:12],
        "description": (listing.get("description") or None),
    }
    # Этап 4: сигналы рынка — история цены и ликвидность (копятся рескрейпом)
    from krisha.market import days_on_market, liquidity_estimate, price_history_points

    result["price_history"] = price_history_points(listing.get("id"))
    result["days_on_market"] = days_on_market(listing.get("id"))
    # diff_pct подмешивает «срок продажи» по ценовой полосе: похожие по цене
    result["liquidity"] = liquidity_estimate(
        listing.get("district"), listing.get("rooms"), result.get("diff_pct")
    )

    # Скам-детектор: цена сильно ниже оценки + эвристики по объявлению
    from krisha.scam import assess_scam_risk

    result["scam_risk"] = assess_scam_risk(
        listing, result["fair_price"], result.get("actual_price")
    )

    # Оценка ремонта по фото (Gemini Vision, кэш + fail-soft).
    # live только когда flags_live=True (бот); веб получает из кэша.
    from krisha.vision import assess_renovation

    try:
        result["renovation"] = assess_renovation(listing, live=flags_live)
    except Exception:  # noqa: BLE001 — фото-анализ не должен ломать оценку
        logger.exception("vision failed")
        result["renovation"] = None

    # Аналоги: похожие активные объявления из базы (kNN по фичам, fail-soft)
    from krisha.analogs import find_analogs

    try:
        result["analogs"] = find_analogs(listing)
    except Exception:  # noqa: BLE001 — аналоги не должны ломать оценку
        logger.exception("analogs failed")
        result["analogs"] = []

    # Этап 5: LLM-анализ описания — бейджи red flags / плюсов (кэш + Gemini).
    # flags_live=False — быстрый ответ: отдаём только кэш, а если кэша нет,
    # ставим flags_pending=True и фронт догружает флаги отдельным запросом.
    import os

    from krisha.llm_flags import GEMINI_API_KEY_ENV

    result["text_flags"] = flags_to_badges(raw_flags)
    text = listing.get("description") or ""
    result["flags_pending"] = bool(
        not flags_live
        and len(text.strip()) >= 20
        and raw_flags is None
        and os.environ.get(GEMINI_API_KEY_ENV)
    )
    return result


def predict_from_url(url: str, flags_live: bool = True) -> dict[str, Any]:
    match = KRISHA_URL_RE.search(url)
    if not match:
        raise ValueError("Ожидается ссылка вида https://krisha.kz/a/show/<id>")
    # Защита от SSRF: не ходим по сырому пользовательскому URL (можно подсунуть
    # http://169.254.169.254/krisha.kz/a/show/1 — подстрока пройдёт проверку).
    # Берём только id и собираем канонический адрес сами, как в боте.
    url = f"{{https://krisha.kz/a/show/{match.group(1}})}"
    # Короткий бюджет: это пользовательский запрос, а не краулер — при 403/429
    # быстро сдаёмся (worst-case ~6 сек), не держим поток тредпула минутами
    with PoliteClient(delay_range=(0.5, 1.0), max_retries=2, throttle_wait_s=2.0) as client:
        html = client.get(url)
    if html is None:
        raise RuntimeError("Не удалось загрузить объявление")
    listing = parse_detail(html, url)
    if listing is None:
        raise RuntimeError("Не удалось распарсить объявление")
    result = predict_from_listing(listing, flags_live=flags_live)
    # Каждая проверенная ссылка пополняет базу (fail-soft: read-only FS и т.п.)
    from krisha.db import find_duplicate_id, listing_fingerprint, upsert_listing

    result["duplicate_of"] = None
    try:
        result["duplicate_of"] = find_duplicate_id(
            listing_fingerprint(listing), int(listing["id"])
        )
        upsert_listing({**listing, "source": "user"})
    except Exception:  # noqa: BLE001 — сохранение не должно ломать оценку
        logger.warning("predict: не удалось сохранить объявление в базу", exc_info=True)
    return result
