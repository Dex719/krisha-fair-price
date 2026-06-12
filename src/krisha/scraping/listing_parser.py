"""Парсер страницы поиска: вытаскивает id объявлений со страницы выдачи."""

import re

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


CARD_ID_RE = re.compile(r'data-id="(\d+)"')
CARD_PRICE_RE = re.compile(r'class="a-card__price"[^>]*>([^<]+)<')


def parse_listing_prices(html: str) -> dict[int, int | None]:
    """id → цена (₸) по карточкам выдачи. Цена не нашлась → None.

    Используется рескрейпом (этап 4): даёт актуальные цены всей выдачи
    без захода на детальные страницы.
    """
    valid = set(parse_listing_ids(html))  # data-id бывает и у баннеров — фильтруем
    ids = [(m.start(), int(m.group(1))) for m in CARD_ID_RE.finditer(html) if int(m.group(1)) in valid]
    prices = [(m.start(), m.group(1)) for m in CARD_PRICE_RE.finditer(html)]
    out: dict[int, int | None] = {}
    pi = 0
    for idx, (pos, lid) in enumerate(ids):
        end = ids[idx + 1][0] if idx + 1 < len(ids) else len(html)
        price = None
        while pi < len(prices) and prices[pi][0] < pos:
            pi += 1
        if pi < len(prices) and prices[pi][0] < end:
            digits = re.sub(r"\D", "", prices[pi][1])
            price = int(digits) if digits else None
            pi += 1
        out[lid] = price
    return out


def has_next_page(html: str, current_page: int) -> bool:
    """Есть ли ссылка на следующую страницу в пагинаторе."""
    return f"page={current_page + 1}" in html
