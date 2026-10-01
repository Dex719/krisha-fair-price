"""Спецификации моделей: продажа (₸) и аренда (₸/мес).

Конвейер обучения один (krisha.train), а различается то, что перечислено
здесь: откуда данные, какие границы цен считаются мусором, на каких фичах
учимся и куда пишутся артефакты. У аренды свой каталог артефактов, поэтому
ретрейн одной модели не может перезаписать файлы другой.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from krisha.config import (
    DB_PATH,
    METRICS_HISTORY_PATH,
    MODEL_GATE_SAMPLES_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    MODEL_QUANTILE_PATH,
    MODELS_DIR,
    PPSM_MAX,
    PPSM_MIN,
    PRICE_MAX,
    PRICE_MIN,
    RENT_DB_PATH,
    RENT_METRICS_HISTORY_PATH,
    RENT_MODEL_GATE_SAMPLES_PATH,
    RENT_MODEL_META_PATH,
    RENT_MODEL_PATH,
    RENT_MODEL_QUANTILE_PATH,
    RENT_MODELS_DIR,
    RENT_PPSM_MAX,
    RENT_PPSM_MIN,
    RENT_PRICE_MAX,
    RENT_PRICE_MIN,
    RENT_SPATIAL_REF_PATH,
    REPORTS_DIR,
    SPATIAL_REF_PATH,
)
from krisha.features import CAT_FEATURES, NUM_FEATURES, RENT_CAT_FEATURES, RENT_NUM_FEATURES


@dataclass(frozen=True)
class ModelSpec:
    deal: str  # как в URL выдачи krisha: "prodazha" / "arenda"
    label: str
    db_path: Path
    models_dir: Path
    model_path: Path
    quantile_path: Path
    meta_path: Path
    spatial_ref_path: Path
    gate_samples_path: Path
    metrics_history_path: Path
    shap_path: Path
    num_features: tuple[str, ...]
    cat_features: tuple[str, ...]
    price_bounds: tuple[float, float]
    ppsm_bounds: tuple[float, float]

    @property
    def all_features(self) -> list[str]:
        return [*self.num_features, *self.cat_features]

    @property
    def is_rent(self) -> bool:
        return self.deal == "arenda"


SALE = ModelSpec(
    deal="prodazha",
    label="продажа",
    db_path=DB_PATH,
    models_dir=MODELS_DIR,
    model_path=MODEL_PATH,
    quantile_path=MODEL_QUANTILE_PATH,
    meta_path=MODEL_META_PATH,
    spatial_ref_path=SPATIAL_REF_PATH,
    gate_samples_path=MODEL_GATE_SAMPLES_PATH,
    metrics_history_path=METRICS_HISTORY_PATH,
    shap_path=REPORTS_DIR / "shap_summary.png",
    num_features=tuple(NUM_FEATURES),
    cat_features=tuple(CAT_FEATURES),
    price_bounds=(PRICE_MIN, PRICE_MAX),
    ppsm_bounds=(PPSM_MIN, PPSM_MAX),
)

RENT = ModelSpec(
    deal="arenda",
    label="аренда",
    db_path=RENT_DB_PATH,
    models_dir=RENT_MODELS_DIR,
    model_path=RENT_MODEL_PATH,
    quantile_path=RENT_MODEL_QUANTILE_PATH,
    meta_path=RENT_MODEL_META_PATH,
    spatial_ref_path=RENT_SPATIAL_REF_PATH,
    gate_samples_path=RENT_MODEL_GATE_SAMPLES_PATH,
    metrics_history_path=RENT_METRICS_HISTORY_PATH,
    shap_path=REPORTS_DIR / "shap_summary_rent.png",
    num_features=tuple(RENT_NUM_FEATURES),
    cat_features=tuple(RENT_CAT_FEATURES),
    price_bounds=(RENT_PRICE_MIN, RENT_PRICE_MAX),
    ppsm_bounds=(RENT_PPSM_MIN, RENT_PPSM_MAX),
)

SPECS = {SALE.deal: SALE, RENT.deal: RENT}


def spec_for(deal: str) -> ModelSpec:
    try:
        return SPECS[deal]
    except KeyError:
        raise ValueError(f"Неизвестный тип сделки {deal!r}: ожидаем {sorted(SPECS)}") from None
