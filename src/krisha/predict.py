"""Предсказание справедливой цены: по URL объявления или по готовому dict.

Вердикты: GOOD_DEAL (дешевле модели на >10%), OVERPRICED (дороже на >10%),
FAIR — в пределах ±10%.
"""

import json
import logging
import re
import sqlite3
from functools import lru_cache
from typing import Any

import httpx
import numpy as np
from catboost import CatBoostRegressor, Pool

from krisha.config import (
    DB_PATH,
    MODEL_HI_PATH,
    MODEL_LO_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    MODEL_QUANTILE_PATH,
    feature_vision,
)
from krisha.db import get_conn
from krisha.features import listing_to_frame
from krisha.geo import build_location_details
from krisha.interval import MIN_INTERVAL_WIDTH_LOG, finalize_interval
from krisha.scraping.client import PoliteClient
from krisha.scraping.detail_parser import parse_detail

logger = logging.getLogger(__name__)

class InvalidListingUrl(ValueError):
    """Ссылка не похожа на объявление krisha.kz — ошибка ПОЛЬЗОВАТЕЛЯ.

    Отдельный тип нужен, чтобы api/app.py отдавал 422 с текстом только на
    неё. Раньше ловился любой ValueError, и внутренние сбои (например
    JSONDecodeError на битом model_meta.json — подкласс ValueError) тоже
    превращались в 422 с сырым текстом исключения наружу.
    """


KRISHA_URL_RE = re.compile(r"krisha\.kz/a/show/(\d+)")
KRISHA_SHOW_BASE = "https://krisha.kz/a/show/"
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
    meta = json.loads(MODEL_META_PATH.read_text(encoding="utf-8"))
    return model, meta


class _LegacyQuantilePair:
    """Миграционный фолбэк (issue #132): до следующего retrain на проде ещё
    может лежать старая пара model_lo/model_hi (раздельные Quantile-модели),
    а не новая MODEL_QUANTILE_PATH. Обёртка имитирует интерфейс
    MultiQuantile-модели (`.predict(pool)` → `(n, 2)`), чтобы остальной код
    (predict_from_listing/_apply_cqr) не знал о разнице форматов."""

    def __init__(self, model_lo: CatBoostRegressor, model_hi: CatBoostRegressor) -> None:
        self._lo = model_lo
        self._hi = model_hi

    def predict(self, pool: Pool) -> np.ndarray:
        lo = np.asarray(self._lo.predict(pool))
        hi = np.asarray(self._hi.predict(pool))
        return np.column_stack([lo, hi])


@lru_cache(maxsize=1)
def load_interval_models() -> CatBoostRegressor | _LegacyQuantilePair | None:
    """MultiQuantile-модель (issue #132: одна модель q10+q90 вместо
    model_lo/model_hi) для доверительного интервала цены. `model.predict(pool)`
    возвращает `(n, 2)`: столбец 0 = q10 (lo), столбец 1 = q90 (hi).

    Пока model_quantile.cbm не появился (ждём ближайший еженедельный
    retrain — issue #132 меняет только пайплайн обучения, старые
    опубликованные веса остаются на диске до тех пор), используем старую
    пару model_lo/model_hi через `_LegacyQuantilePair`, чтобы прод не терял
    доверительный интервал и не падал на плоский вердикт ±10%. None — только
    если вообще нет ни новой, ни старой модели (совсем свежий деплой без
    интервала)."""
    if MODEL_QUANTILE_PATH.exists():
        quantile_model = CatBoostRegressor()
        quantile_model.load_model(str(MODEL_QUANTILE_PATH))
        return quantile_model
    if MODEL_LO_PATH.exists() and MODEL_HI_PATH.exists():
        model_lo = CatBoostRegressor()
        model_lo.load_model(str(MODEL_LO_PATH))
        model_hi = CatBoostRegressor()
        model_hi.load_model(str(MODEL_HI_PATH))
        return _LegacyQuantilePair(model_lo, model_hi)
    return None


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
    """Топ-факторы цены для конкретного объявления через SHAP-значения CatBoost.

    issue #118 — что НЕ сработало и почему: пробовал завести кэшированный
    `shap.TreeExplainer` (лежит на процесс, как `load_model()`), в расчёте, что
    он один раз строит какую-то background-статистику и переиспользует её.
    По факту `shap`-обёртка для CatBoost-моделей сама лишь делегирует в тот
    же `model.get_feature_importance(pool, type="ShapValues")` (см.
    `shap/explainers/_tree.py:633` — "thanks to the CatBoost team..."), так что
    per-request стоимость не меняется вообще (замерено: 45.5ms что напрямую,
    что через кэшированный explainer, на синтетической модели 500 деревьев ×
    depth 8) — а новый прямой рантайм-зависимый `shap` (+ numba/llvmlite/
    scikit-learn, ~40+ МБ) на 2 vCPU free tier того не стоит. Реальный рычаг —
    `shap_calc_type="Approximate"` (метод Saabas, single-ordering): то же само
    API, эмпирически ~2.7x быстрее (45ms → ~18ms на той же синтетике). Разница
    не бесплатна: это не точный Shapley (см. предупреждение в доке shap про
    approximate — переоценивает вклад нижних сплитов), и в спот-проверке на
    5 строках порядок топ-5 факторов иногда менялся местами (1 позиция из 5).
    Если для карточки "почему цена такая" это неприемлемо — сообщи, откачу
    на `shap_calc_type="Regular"` (тогда #118 остаётся без реального фикса,
    честно об этом в PR).
    """
    shap_vals = model.get_feature_importance(
        pool, type="ShapValues", shap_calc_type="Approximate"
    )[0][:-1]
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


def _apply_cqr(lo_raw: float, hi_raw: float, interval_meta: dict[str, Any]) -> tuple[float, float]:
    """Расширяет сырой квантильный интервал [lo_raw, hi_raw] (log-цена) по CQR
    и возвращает (log_lo, log_hi).

    Два формата меты, оба должны поддерживаться (issue #105 доработка,
    обратная совместимость с уже опубликованной прод-моделью):
    - новый, нормированный: `cqr_scale` — множитель ширины интервала,
      `[lo, hi] -> [lo - scale*width, hi + scale*width]`. Тот же расчёт, что
      train.py использует при подсчёте `coverage_test`, так что метрика
      гейта совпадает с тем, что реально видит пользователь.
    - старый, у уже опубликованной модели (до ближайшего retrain её meta
      этот PR не переписывает): `cqr_offset_log` — фиксированный лог-сдвиг,
      `[lo, hi] -> [lo - offset, hi + offset]`. Если считать отсутствие
      `cqr_scale` как `scale=0`, интервал резко сузится и вердикты станут
      агрессивнее сразу после деплоя, до ближайшего retrain — поэтому явно
      откатываемся на старую формулу, а не молчаливый scale=0.
    """
    if "cqr_scale" in interval_meta:
        scale = float(interval_meta["cqr_scale"])
        width = max(hi_raw - lo_raw, MIN_INTERVAL_WIDTH_LOG)
        log_lo = lo_raw - scale * width
        log_hi = hi_raw + scale * width
    else:
        offset = float(interval_meta.get("cqr_offset_log", 0.0))
        log_lo = lo_raw - offset
        log_hi = hi_raw + offset
    return max(min(log_lo, 30.0), -30.0), max(min(log_hi, 30.0), -30.0)


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
    listing: dict[str, Any],
    live_vision: bool = True,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """issue #110: `conn` — необязательное уже открытое SQLite-соединение,
    переданное вызывающим кодом (`predict_from_url`/`api/app.py`). Если None,
    открываем и закрываем его сами — этот путь остаётся рабочим и для прямых
    вызовов (тесты, `alerts.find_good_deals`), просто без экономии на числе
    соединений. Раньше на один запрос уходило ~8 отдельных `get_conn()`
    (llm_flags кэш, price_history, days_on_market, liquidity, vision-кэш,
    analogs, log_prediction) — теперь один на всю функцию."""
    if conn is None:
        with get_conn(DB_PATH) as new_conn:
            return _predict_from_listing(listing, live_vision, new_conn)
    return _predict_from_listing(listing, live_vision, conn)


def _predict_from_listing(
    listing: dict[str, Any], live_vision: bool, conn: sqlite3.Connection
) -> dict[str, Any]:
    model, meta = load_model()
    features = meta["features"]

    from krisha.spatial import load_spatial_ref

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

    # Доверительный интервал: MultiQuantile-модель (issue #132) + CQR-сдвиг из меты.
    fair_low = fair_high = None
    quantile_model = load_interval_models()
    if quantile_model is not None:
        interval_meta = meta.get("metrics", {}).get("interval", {})
        quantile_pred = quantile_model.predict(pool)[0]
        lo_raw, hi_raw = float(quantile_pred[0]), float(quantile_pred[1])
        log_lo, log_hi = _apply_cqr(lo_raw, hi_raw, interval_meta)
        fair_low = float(np.expm1(log_lo))
        fair_high = float(np.expm1(log_hi))
        fair_low, fair_high = finalize_interval(fair_price, fair_low, fair_high)

    actual = listing.get("price")
    if actual and quantile_model is not None:
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

    result["price_history"] = price_history_points(listing.get("id"), conn=conn)
    result["days_on_market"] = days_on_market(listing.get("id"), conn=conn)
    # diff_pct подмешивает «срок продажи» по ценовой полосе: похожие по цене
    result["liquidity"] = liquidity_estimate(
        listing.get("district"), listing.get("rooms"), result.get("diff_pct"), conn=conn
    )

    # issue #157: предупреждение о подозрительно низкой цене — от НИЖНЕЙ
    # ГРАНИЦЫ интервала, а не от точечной оценки. Граница откалибрована CQR
    # под фактическое покрытие, то есть «ниже неё» — проверяемое утверждение,
    # а не выдуманный порог в процентах. days_on_market посчитан выше.
    from krisha.scam import assess_scam_risk

    result["scam_risk"] = assess_scam_risk(
        result.get("fair_price_low"),
        result.get("actual_price"),
        result.get("days_on_market"),
    )

    # Оценка ремонта по фото — за фича-флагом (issue #157): вклад в точность
    # не измерен, а каждый показ это живой запрос к Gemini Vision прямо на
    # пользовательском пути. Вернуть, когда абляция покажет измеримый вклад.
    if feature_vision():
        from krisha.vision import assess_renovation

        try:
            result["renovation"] = assess_renovation(listing, live=live_vision, conn=conn)
        except Exception:  # noqa: BLE001 — фото-анализ не должен ломать оценку
            logger.exception("vision failed")
            result["renovation"] = None
    else:
        result["renovation"] = None

    # Аналоги: похожие активные объявления из базы (kNN по фичам, fail-soft)
    from krisha.analogs import find_analogs

    try:
        result["analogs"] = find_analogs(listing, conn=conn)
    except Exception:  # noqa: BLE001 — аналоги не должны ломать оценку
        logger.exception("analogs failed")
        result["analogs"] = []

    # issue #157: LLM-бейджи описания убраны с пользовательского пути целиком.
    # Абляция на честном сплите показала, что как фичи они УХУДШАЮТ модель
    # (R² 0.79 → 0.76), в неё они не идут, и оставались только украшением
    # карточки — ценой похода в Gemini на каждый предикт, кэша в SQLite
    # (который всё равно стирался при каждом рестарте Space) и отдельного
    # эндпоинта с догрузкой на фронте. Модуль krisha.llm_flags и пакетный
    # scripts/analyze_flags.py оставлены для офлайн-абляции: условие возврата
    # — измеримый вклад на rolling-origin backtest (#158).

    # issue #128: логируем каждый предикт (пользовательский через
    # predict_from_url, канальный через alerts.find_good_deals — оба
    # заходят сюда) — без этого нельзя проверить, работают ли вердикты:
    # через месяц сравниваем price drift/days-on-market по verdict.
    from krisha.db import log_prediction

    try:
        log_prediction(
            listing.get("id"),
            result.get("fair_price"),
            result.get("fair_price_low"),
            result.get("fair_price_high"),
            result.get("verdict"),
            meta.get("metrics", {}).get("trained_at"),
            conn=conn,
        )
    except Exception:  # noqa: BLE001 — лог предикта не должен ломать оценку
        logger.exception("log_prediction failed")
    return result


def predict_from_url(
    url: str, live_vision: bool = True, timeout: "float | httpx.Timeout | None" = None
) -> dict[str, Any]:
    match = KRISHA_URL_RE.search(url)
    if not match:
        raise InvalidListingUrl("Ожидается ссылка вида https://krisha.kz/a/show/<id>")
    # Защита от SSRF: не ходим по сырому пользовательскому URL (можно подсунуть
    # http://169.254.169.254/krisha.kz/a/show/1 — подстрока пройдёт проверку).
    # Берём только id и собираем канонический адрес сами, как в боте.
    url = KRISHA_SHOW_BASE + match.group(1)
    # Короткий бюджет: это пользовательский запрос, а не краулер. Мало
    # ограничить ретраи (403/429 отдаются быстро) — нужен и короткий сетевой
    # таймаут: на ПОВИСШЕМ коннекте два ретрая по REQUEST_TIMEOUT=30 с давали
    # больше минуты на один запрос. timeout прокидывает вызывающий
    # (predict_gate.user_timeout: 5 с всего, 3 с на connect).
    with PoliteClient(
        delay_range=(0.5, 1.0), max_retries=2, throttle_wait_s=2.0, timeout=timeout
    ) as client:
        html = client.get(url)
    if html is None:
        raise RuntimeError("Не удалось загрузить объявление")
    listing = parse_detail(html, url)
    if listing is None:
        raise RuntimeError("Не удалось распарсить объявление")
    # issue #110: одно SQLite-соединение на весь HTTP-запрос — предикт
    # (llm_flags/market/vision/analogs/log_prediction) и последующие
    # find_duplicate_id/upsert_listing переиспользуют его вместо каждый
    # своего get_conn().
    from krisha.db import find_duplicate_id, listing_fingerprint, upsert_listing

    with get_conn(DB_PATH) as conn:
        result = predict_from_listing(listing, live_vision=live_vision, conn=conn)
        # Каждая проверенная ссылка пополняет базу (fail-soft: read-only FS и т.п.)
        result["duplicate_of"] = None
        try:
            result["duplicate_of"] = find_duplicate_id(
                listing_fingerprint(listing), int(listing["id"]), conn=conn
            )
            upsert_listing({**listing, "source": "user"}, conn=conn)
        except Exception:  # noqa: BLE001 — сохранение не должно ломать оценку
            logger.warning("predict: не удалось сохранить объявление в базу", exc_info=True)
    return result
