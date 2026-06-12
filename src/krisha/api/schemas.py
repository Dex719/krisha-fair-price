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
    details: list[DetailItem] = []  # характеристики объявления (этаж, год, ремонт...)
    photos: list[str] = []          # URL фото с krisha-photos.kcdn.online
    description: str | None = None


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
