"""Этап 4 роадмапа: регулярный рескрейп → история цены и ликвидность.

Один проход (sweep) обходит страницы выдачи и по карточкам (без детальных
страниц) обновляет базу:

- знакомое объявление: last_seen=now, цена изменилась → точка в price_history;
- новое объявление: детальная страница → upsert + стартовая точка истории;
- знакомое, но давно не виденное (DELIST_AFTER_DAYS): is_active=0 —
  считаем проданным/снятым, разница last_seen-first_seen = дни на рынке.

Запуск: `python scripts/rescrape.py` (по расписанию — ежедневно).
"""

import logging

from krisha.config import SEARCH_URL
from krisha.db import (
    DB_PATH,
    _record_price_if_changed,
    get_conn,
    init_db,
    known_ids,
    upsert_listing,
)
from krisha.scraping.client import PoliteClient
from krisha.scraping.detail_parser import parse_detail
from krisha.scraping.listing_parser import has_next_page, parse_listing_prices

logger = logging.getLogger(__name__)

DELIST_AFTER_DAYS = 3  # не видели в выдаче N дней → считаем снятым


def sweep(max_pages: int = 400, max_new_details: int = 300, db_path=DB_PATH) -> dict:
    """Один проход рескрейпа. Возвращает счётчики для лога/отчёта."""
    init_db(db_path)
    seen_in_db = known_ids(db_path)
    found: dict[int, int | None] = {}

    with PoliteClient() as client:
        for page in range(1, max_pages + 1):
            url = SEARCH_URL if page == 1 else f"{SEARCH_URL}?page={page}"
            html = client.get(url)
            if html is None:
                logger.error("Стр. %s не загрузилась — стоп обхода", page)
                break
            found.update(parse_listing_prices(html))
            if not has_next_page(html, page):
                break

        new_ids = [lid for lid in found if lid not in seen_in_db][:max_new_details]
        new_count = 0
        for lid in new_ids:
            detail_html = client.get(f"https://krisha.kz/a/show/{lid}")
            listing = parse_detail(detail_html, f"https://krisha.kz/a/show/{lid}") if detail_html else None
            if listing is not None:
                upsert_listing(listing, db_path)
                new_count += 1

    price_changes = 0
    known_seen = [lid for lid in found if lid in seen_in_db]
    with get_conn(db_path) as conn:
        for lid in known_seen:
            conn.execute(
                "UPDATE listings SET last_seen = datetime('now'), is_active = 1, "
                "delisted_at = NULL WHERE id = ?",
                (lid,),
            )
            price = found[lid]
            if price is not None and _record_price_if_changed(conn, lid, price):
                conn.execute("UPDATE listings SET price = ? WHERE id = ?", (price, lid))
                price_changes += 1

        delisted = conn.execute(
            "UPDATE listings SET is_active = 0, delisted_at = datetime('now') "
            "WHERE is_active = 1 "
            f"AND julianday('now') - julianday(last_seen) > {DELIST_AFTER_DAYS} "
            "RETURNING id",
        ).fetchall()

    stats = {
        "found_in_search": len(found),
        "known_seen": len(known_seen),
        "new_listings": new_count,
        "price_changes": price_changes,
        "delisted": len(delisted),
    }
    logger.info(
        "Рескрейп: в выдаче %(found_in_search)s, знакомых %(known_seen)s, "
        "новых %(new_listings)s, изменений цены %(price_changes)s, снято %(delisted)s",
        stats,
    )
    return stats
