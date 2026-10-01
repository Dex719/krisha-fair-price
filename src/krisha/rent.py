"""Особенности арендных объявлений, общие для обучения и оценки.

Подселение и сдача комнаты. В выдаче «Аренда квартир» krisha попадаются
объявления вида «ищу третью девушку на подселение» или «сдам комнату»: в
карточке — целая квартира (2 комнаты, 60 м²), а цена — за койку или комнату.
Таких ~0.7% базы, но в тесте их ошибка ~78%: модель честно оценивает
квартиру, а цена в объявлении — не за неё. Из обучения их убираем, а при
оценке такой ссылки предупреждаем, что сравнение неприменимо.
"""

from __future__ import annotations

import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

# Подобрано на базе аренды (октябрь 2026, 47k описаний): ловит подселение,
# койко-место, «сдам комнату», поиск соседей — в том числе по-казахски.
# «бөлме» (казахское «комната») сюда НЕ входит: «2 бөлмелі пәтер» — это
# обычная двушка, на нём регулярка ловила сотни ложных срабатываний.
ROOM_SHARE_RE = re.compile(
    r"подсел|сожител|совместно[гйм]?[оу]? прожив|койко|за место|место в комнат|"
    r"с человека|с каждого|каждая по|каждый по|ищу девуш|ищем девуш|ищу третью|"
    r"нужна девушка|соседк|соседа|кыз керек|қыз керек|бала керек|бірге тұр|"
    r"бирге тур|сда[мюё]т?с?я? комнат|сда[её]тся комнат|комнату девушк|"
    r"комнату парн|хостел",
    re.IGNORECASE,
)


# Тип сделки по странице объявления. URL у продажи и аренды одинаковый
# (/a/show/<id>), categoryAlias тоже («kvartiry»). Надёжнее всего — набор
# параметров: у аренды свои ключи (who_match, flat.rent_renovation, ...), у
# продажи свои (has_change у 99% лотов, house.year, ...). Цена — запасной
# признак: аренда стоит тысячи–миллионы ₸ в месяц, продажа от 5 млн ₸.
# На базах (47k аренды + 98k продажи, октябрь 2026) правило ошибается на
# одном лоте из каждой.
RENT_PARAM_KEYS = frozenset({
    "who_match", "flat.rent_renovation", "flat.facilities", "flat.furniture",
    "bathroom", "separated_toilet", "window_side", "kitchen_studio",
    "toilet_count", "balcony_count", "loggia_count",
})
SALE_PARAM_KEYS = frozenset({
    "has_change", "house.year", "flat.building", "ceiling", "flat.renovation",
    "flat.toilet", "flat.door", "flat.flooring",
})
# Ниже — аренда даже при продажных ключах: старый формат арендной страницы
# (~1% лотов) показывал flat.renovation; продажи дешевле 3 млн ₸ в Алматы нет.
RENT_PRICE_CEILING = 3_000_000
SALE_PRICE_FLOOR = 5_000_000


def detect_deal(listing: dict) -> str:
    """«arenda» или «prodazha» для распарсенного объявления (detail_parser)."""
    import json

    explicit = listing.get("deal")
    if explicit in ("arenda", "prodazha"):
        return explicit
    price = listing.get("price")
    if price is not None and price < RENT_PRICE_CEILING:
        return "arenda"
    raw = listing.get("raw_params") or "{}"
    try:
        keys = set(json.loads(raw) if isinstance(raw, str) else raw)
    except (TypeError, ValueError):
        keys = set()
    if keys & RENT_PARAM_KEYS:
        return "arenda"
    if keys & SALE_PARAM_KEYS:
        return "prodazha"
    if price is not None and price < SALE_PRICE_FLOOR:
        return "arenda"
    return "prodazha"


def is_room_share(description: str | None) -> bool:
    """Похоже на подселение / сдачу комнаты, а не квартиры целиком."""
    return bool(description) and bool(ROOM_SHARE_RE.search(str(description)))


def drop_room_shares(df: pd.DataFrame) -> pd.DataFrame:
    """Убирает из обучающей выборки подселения и сдачу комнат."""
    if "description" not in df.columns:
        return df
    mask = df["description"].map(is_room_share)
    dropped = int(mask.sum())
    if dropped:
        logger.info("Аренда: убрано %d подселений/комнат (цена не за квартиру)", dropped)
    return df.loc[~mask].reset_index(drop=True)
