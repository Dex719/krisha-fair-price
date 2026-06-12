"""Pydantic-схемы API."""

from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    url: str = Field(..., description="Ссылка на объявление, например https://krisha.kz/a/show/123456789")


class Factor(BaseModel):
    feature: str
    impact: float  # вклад в log(price): >0 — повышает цену, <0 — понижает


class DetailItem(BaseModel):
    label: str  # человекочитаемая подпись («Год постройки»)
    value: str


class PricePoint(BaseModel):
    price: int
    observed_at: str


class Liquidity(BaseModel):
    median_days: int  # медиана дней до снятия у похожих (район + комнаты)
    sample: int       # размер выборки снятых аналогов


class TextFlag(BaseModel):
    kind: str   # warn — настораживает, plus — скрытый плюс из текста
    label: str  # подпись бейджа («Срочная продажа», «Торг уместен»...)


class PredictResponse(BaseModel):
    listing_id: int | None
    url: str | None
    title: str | None
    address: str | None
    actual_price: int | None
    fair_price: float
    verdict: str | None  # GOOD_DEAL / FAIR / OVERPRICED
    diff_pct: float | None
    top_factors: list[Factor]
    details: list[DetailItem] = []          # характеристики объявления (этаж, год, ремонт...)
    complex_details: list[DetailItem] = []  # этап 2: блок «О доме» из справочника ЖК
    location_details: list[DetailItem] = [] # этап 3: блок «Локация» (walk score, POI)
    price_history: list[PricePoint] = []    # этап 4: точки истории цены
    days_on_market: int | None = None       # этап 4: дней в выдаче
    liquidity: Liquidity | None = None      # этап 4: за сколько продаются аналоги
    text_flags: list[TextFlag] = []         # этап 5: LLM-анализ описания
    photos: list[str] = []                  # URL фото с krisha-photos.kcdn.online
    description: str | None = None


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
