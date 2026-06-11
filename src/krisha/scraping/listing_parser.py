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


def has_next_page(html: str, current_page: int) -> bool:
    """Есть ли ссылка на следующую страницу в пагинаторе."""
    return f"page={current_page + 1}" in html
