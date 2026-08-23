"""Pydantic-схемы API."""

from typing import Literal

from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    # max_length: URL объявления укладывается в ~60 символов, всё длиннее — мусор
    url: str = Field(
        ...,
        max_length=500,
        description="Ссылка на объявление, например https://krisha.kz/a/show/123456789",
    )


class Factor(BaseModel):
    feature: str
    impact: float  # вклад в log(price): >0 — повышает цену, <0 — понижает
    impact_pct: float | None = None  # тот же вклад как множитель цены, в %
    impact_tenge: float | None = None  # оценка вклада в итоговую цену, ₸
    hint: str | None = None  # подсказка со статистикой рынка (тултип на сайте)


class ScamRisk(BaseModel):
    level: str        # medium / high
    below_pct: float  # насколько ниже НИЖНЕЙ границы интервала, %
    reasons: list[str] = Field(default_factory=list)


class Analog(BaseModel):
    id: int
    url: str
    title: str | None = None
    price: float
    area: float
    rooms: int | None = None
    floor: int | None = None
    total_floors: int | None = None
    year_built: int | None = None
    district: str | None = None
    ppsm: float | None = None  # цена за м²


class DetailItem(BaseModel):
    label: str  # человекочитаемая подпись («Год постройки»)
    value: str


class PricePoint(BaseModel):
    price: int
    observed_at: str


class Liquidity(BaseModel):
    median_days: int  # медиана дней до снятия у похожих (снятие != продажа)
    sample: int       # размер выборки снятых аналогов
    scope: str = "district_rooms"        # уровень оценки: район+комнаты или city
    band: str | None = None              # ценовая полоса объявления: below/near/above
    band_median_days: int | None = None  # «похожие по цене уходят за ~N дней»
    band_sample: int | None = None       # выборка снятых в этой полосе


class Renovation(BaseModel):
    level: str                              # rough/needs_repair/dated/good/premium
    label: str                              # подпись по-русски
    comment: str | None = None


class PredictResponse(BaseModel):
    listing_id: int | None
    url: str | None
    title: str | None
    address: str | None
    actual_price: int | None
    fair_price: float
    fair_price_low: float | None = None   # нижняя граница интервала (q10, CQR)
    fair_price_high: float | None = None  # верхняя граница интервала (q90, CQR)
    verdict: str | None  # GOOD_DEAL / FAIR / OVERPRICED
    diff_pct: float | None
    top_factors: list[Factor]
    details: list[DetailItem] = Field(default_factory=list)          # характеристики объявления (этаж, год, ремонт...)
    complex_details: list[DetailItem] = Field(default_factory=list)  # этап 2: блок «О доме» из справочника ЖК
    location_details: list[DetailItem] = Field(default_factory=list) # этап 3: блок «Локация» (walk score, POI)
    price_history: list[PricePoint] = Field(default_factory=list)    # этап 4: точки истории цены
    days_on_market: int | None = None       # этап 4: дней в выдаче
    liquidity: Liquidity | None = None      # этап 4: за сколько продаются аналоги
    duplicate_of: int | None = None         # возможный дубль (тот же «отпечаток» квартиры)
    photos: list[str] = Field(default_factory=list)                  # URL фото с krisha-photos.kcdn.online
    description: str | None = None
    analogs: list[Analog] = Field(default_factory=list)              # похожие активные объявления (kNN)
    scam_risk: ScamRisk | None = None       # бейдж «подозрительно дёшево»
    renovation: Renovation | None = None    # оценка ремонта по фото — за FEATURE_VISION (#157)


class DemoResponse(BaseModel):
    listing_id: int
    url: str


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_error_pct: float | None = None
    model_median_error_pct: float | None = None  # медианная ошибка (MDAPE)
    model_r2: float | None = None  # R² на отложенной выборке — доля, не проценты
    model_mae: float | None = None  # средняя абсолютная ошибка, ₸
    # issue #158: подтверждена ли временная валидность оценки. False означает,
    # что число точности описывает текущий сток, а не экстраполяцию вперёд —
    # страница обязана сказать это вслух, а не показывать голый процент.
    model_temporal_validity: bool | None = None
    data_age_hours: float | None = None
    freshness: Literal["ok", "stale"] = "stale"
    # Статус Telegram-webhook: ok | unset | mismatch | no_token | no_public_url |
    # unknown (не удалось спросить Telegram). Позволяет диагностировать бота
    # снаружи, без доступа к логам хостинга.
    tg_webhook: str = "unknown"
