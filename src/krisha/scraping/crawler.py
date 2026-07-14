"""Краулер: страницы выдачи → id → детальные страницы → SQLite.

Возобновляемый: уже сохранённые id пропускаются, можно прерывать Ctrl+C
и запускать снова. Запуск: `python scripts/crawl.py --pages 50`.
"""

import logging

from krisha.config import SEARCH_URL
from krisha.db import count_listings, init_db, known_ids, upsert_listing
from krisha.scraping.client import BanDetected, PoliteClient
from krisha.scraping.detail_parser import parse_detail
from krisha.scraping.listing_parser import has_next_page, parse_listing_ids

logger = logging.getLogger(__name__)


def crawl(max_pages: int = 50, max_listings: int | None = None, skip_known: bool = True) -> int:
    """Обходит выдачу и сохраняет объявления. Возвращает число новых записей."""
    init_db()
    seen = known_ids() if skip_known else set()
    logger.info("В базе уже %s объявлений", len(seen))
    new_count = 0

    with PoliteClient() as client:
        for page in range(1, max_pages + 1):
            url = SEARCH_URL if page == 1 else f"{SEARCH_URL}?page={page}"
            try:
                html = client.get(url)
            except BanDetected as exc:
                logger.critical("%s — стоп краула", exc)
                break
            if html is None:
                logger.error("Не удалось получить страницу выдачи %s — стоп", page)
                break

            ids = parse_listing_ids(html)
            fresh = [i for i in ids if i not in seen]
            logger.info("Стр. %s: %s объявлений, новых %s", page, len(ids), len(fresh))

            banned = False
            for lid in fresh:
                detail_url = f"https://krisha.kz/a/show/{lid}"
                try:
                    detail_html = client.get(detail_url)
                except BanDetected as exc:
                    logger.critical("%s — стоп краула", exc)
                    banned = True
                    break
                if detail_html is None:
                    continue
                listing = parse_detail(detail_html, detail_url)
                if listing is None:
                    continue
                upsert_listing(listing)
                seen.add(lid)
                new_count += 1
                if new_count % 25 == 0:
                    logger.info("Сохранено новых: %s (всего в базе %s)", new_count, count_listings())
                if max_listings and new_count >= max_listings:
                    logger.info("Достигнут лимит %s — стоп", max_listings)
                    return new_count
            if banned:
                break

            if not has_next_page(html, page):
                logger.info("Дальше страниц нет — стоп")
                break

    logger.info("Готово: +%s новых, всего %s", new_count, count_listings())
    return new_count
