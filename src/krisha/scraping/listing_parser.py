"""Парсер страницы поиска: вытаскивает id объявлений со страницы выдачи."""

import re

from bs4 import BeautifulSoup

LISTING_LINK_RE = re.compile(r'href="/a/show/(\d+)"')


def parse_listing_ids(html: str) -> list[int]:
    """Все уникальные id объявлений на странице выдачи (в порядке появления)."""
    seen: set[int] = set()
    ids: list[int] = []
    for m in LISTING_LINK_RE.finditer(html):
        lid = int(m.group(1))
        if lid not in seen:
            seen.add(lid)
            ids.append(lid)
    return ids


def parse_listing_prices(html: str) -> dict[int, int | None]:
    """id → цена (₸) по карточкам выдачи. Цена не нашлась → None.

    Используется рескрейпом (этап 4): даёт актуальные цены всей выдачи
    без захода на детальные страницы.

    Структурный DOM-парсинг (issue #98): цена ищется ВНУТРИ узла карточки
    (`.a-card`), а не по позиции id/цены в сыром тексте страницы. Раньше
    матчинг был позиционный («цена — первая, что встретилась между текущим
    data-id и следующим»): промо-карточка, переставленные блоки или лишний
    data-id (например, у кнопки избранного) молча сдвигали цену на соседнее
    объявление — и это записывалось в price_history как «изменение цены».
    """
    valid = set(parse_listing_ids(html))  # data-id бывает и у баннеров — фильтруем
    soup = BeautifulSoup(html, "lxml")
    out: dict[int, int | None] = {}
    for card in soup.find_all(class_="a-card"):
        raw_id = card.get("data-id")
        if raw_id is None or not str(raw_id).isdigit():
            continue
        lid = int(raw_id)
        if lid not in valid:
            continue
        price_el = card.find(class_="a-card__price")
        price = None
        if price_el is not None:
            digits = re.sub(r"\D", "", price_el.get_text())
            price = int(digits) if digits else None
        out[lid] = price
    return out


def has_next_page(html: str, current_page: int) -> bool:
    """Есть ли ссылка на следующую страницу в пагинаторе."""
    return f"page={current_page + 1}" in html
