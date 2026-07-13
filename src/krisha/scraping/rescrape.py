"""Этап 4 роадмапа: регулярный рескрейп → история цены и ликвидность.

Один проход (sweep) обходит выдачу и по карточкам (без детальных
страниц) обновляет базу:

- знакомое объявление: last_seen=now, цена изменилась → точка в price_history;
- новое объявление: детальная страница → upsert + стартовая точка истории;
- знакомое, но давно не виденное (DELIST_AFTER_DAYS): is_active=0 —
  считаем проданным/снятым, разница last_seen-first_seen = дни на рынке.

Выдача шардируется по фильтрам «район × комнаты» (см. shard_urls):
общая выдача Алматы обрезается пагинацией и отдаёт только «популярные»
~7-8к объявлений, а 32 шарда суммарно покрывают почти все ~44к.

Запуск: `python scripts/rescrape.py` (по расписанию — ежедневно).
"""

import json
import logging
import statistics
from pathlib import Path

from krisha.config import ALMATY_DISTRICT_SLUGS, BASE_URL, DATA_DIR, ROOM_SHARDS
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

# Сигнатуры анти-бот/капча-страниц (нижний регистр) — сервер отдал HTTP 200,
# но это не выдача. Проверяются только на первой странице шарда, чтобы не
# гонять re.search по мегабайтам HTML на каждой странице.
_ANTIBOT_SIGNS = (
    "captcha",
    "recaptcha",
    "attention required",
    "cf-browser-verification",
    "доступ ограничен",
    "подтвердите, что вы не робот",
)

# История found_in_search последних проходов — для детекта проседания
# parse-rate (issue #97). Отдельный файл на deal, чтобы продажа и аренда
# не путали друг другу медиану.
PARSE_RATE_HISTORY_LEN = 7
PARSE_RATE_DROP_RATIO = 0.5  # алерт, если текущий проход < 50% медианы истории


def _history_path(deal: str) -> Path:
    return DATA_DIR / f"rescrape_history_{deal}.json"


def _load_history(deal: str) -> list[int]:
    path = _history_path(deal)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [int(x) for x in data] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def _save_history(deal: str, history: list[int]) -> None:
    path = _history_path(deal)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history[-PARSE_RATE_HISTORY_LEN:]), encoding="utf-8")


def _looks_like_antibot(html: str) -> bool:
    lower = html.lower()
    return any(sign in lower for sign in _ANTIBOT_SIGNS)


def shard_urls(deal: str = "prodazha") -> list[tuple[str, str]]:
    """Шарды выдачи: (метка, URL первой страницы с фильтрами).

    Район (8) × комнаты (1/2/3/4+) = 32 шарда, у каждого своя пагинация.
    Страница N шарда: `{url}&page={N}` (в URL уже есть query-параметры).

    deal: "prodazha" (продажа) или "arenda" (долгосрочная аренда, цена = ₸/мес;
    разметка выдачи и детальных страниц идентична продаже — парсеры общие).
    """
    shards: list[tuple[str, str]] = []
    for district, slug in ALMATY_DISTRICT_SLUGS.items():
        for rooms, values in ROOM_SHARDS.items():
            query = "&".join(f"das[live.rooms][]={v}" for v in values)
            shards.append(
                (f"{district} {rooms}", f"{BASE_URL}/{deal}/kvartiry/{slug}/?{query}")
            )
    return shards


def _sweep_shard(
    client: PoliteClient,
    label: str,
    base_url: str,
    max_pages: int,
    found: dict[int, int | None],
) -> bool:
    """Обходит пагинацию одного шарда, дописывает id→цена в found.

    Возвращает False, если шард не покрыт: страница не загрузилась
    (сеть/блокировка), похожа на анти-бот/капчу, или первая страница дала
    0 валидных id (сервер отдал 200, но не выдачу — изменённая вёрстка и
    т.п.). Пустой/анти-бот шард НЕ считается покрытием — иначе живые
    объявления рискуют быть ложно помечены delisted (issue #96).
    """
    before = len(found)
    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}&page={page}"
        html = client.get(url)
        if html is None:
            logger.error("Шард «%s»: стр. %s не загрузилась — стоп шарда", label, page)
            return False
        if page == 1 and _looks_like_antibot(html):
            logger.error("Шард «%s»: похоже на анти-бот/капча страницу — стоп шарда", label)
            return False
        page_prices = parse_listing_prices(html)
        if page == 1 and not page_prices:
            logger.warning(
                "Шард «%s»: 0 валидных id на первой странице — подозрительно, шард не покрыт",
                label,
            )
            return False
        found.update(page_prices)
        if not has_next_page(html, page):
            break
    logger.info("Шард «%s»: +%s объявлений (всего %s)", label, len(found) - before, len(found))
    return True


def sweep(
    max_pages: int = 250, max_new_details: int = 300, db_path=DB_PATH, deal: str = "prodazha"
) -> dict:
    """Один проход рескрейпа по всем шардам. Возвращает счётчики для лога/отчёта.

    max_pages — лимит страниц НА ОДИН шард (самый большой шард ~4к объявлений
    ≈ 200 страниц, так что 250 хватает с запасом).
    deal="arenda" — тот же проход по арендной выдаче (обычно в отдельную базу,
    см. RENT_DB_PATH), price = ₸/месяц.
    """
    init_db(db_path)
    seen_in_db = known_ids(db_path)
    found: dict[int, int | None] = {}
    failed_shards: list[str] = []

    with PoliteClient() as client:
        for label, base_url in shard_urls(deal):
            if not _sweep_shard(client, label, base_url, max_pages, found):
                failed_shards.append(label)

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

        # Помечаем снятые только после полного покрытия: если хоть один шард
        # не дообошли (блокировка/сеть), его объявления не получили last_seen,
        # и delisted был бы ложным.
        delisted_count: int | None = 0
        if not failed_shards:
            delisted = conn.execute(
                "UPDATE listings SET is_active = 0, delisted_at = datetime('now') "
                "WHERE is_active = 1 "
                f"AND julianday('now') - julianday(last_seen) > {DELIST_AFTER_DAYS} "
                "RETURNING id",
            ).fetchall()
            delisted_count = len(delisted)
        else:
            logger.warning(
                "Не полностью покрыты шарды: %s — пропускаем пометку delisted",
                ", ".join(failed_shards),
            )
            delisted_count = None

    # Parse-rate история: тихая деградация (сервер отвечает, шарды формально
    # покрыты, но объявлений в разы меньше обычного) не ловится через
    # failed_shards — сравниваем found_in_search с медианой последних проходов
    # (issue #97). Суспишн не считаем при < 3 прошлых точках (нет базы для
    # сравнения — типично для первых запусков/новых deal).
    history = _load_history(deal)
    parse_rate_median = (
        statistics.median(history[-PARSE_RATE_HISTORY_LEN:]) if len(history) >= 3 else None
    )
    suspicious = parse_rate_median is not None and len(found) < parse_rate_median * PARSE_RATE_DROP_RATIO
    if suspicious:
        logger.error(
            "Parse-rate просел: в выдаче %s против медианы %s последних проходов "
            "(порог %.0f%%) — проход помечен подозрительным",
            len(found),
            parse_rate_median,
            PARSE_RATE_DROP_RATIO * 100,
        )
    history.append(len(found))
    _save_history(deal, history)

    stats = {
        "found_in_search": len(found),
        "known_seen": len(known_seen),
        "new_listings": new_count,
        "price_changes": price_changes,
        "delisted": delisted_count,
        "failed_shards": failed_shards,
        "parse_rate_median_7": parse_rate_median,
        "suspicious": suspicious,
    }
    logger.info(
        "Рескрейп: в выдаче %(found_in_search)s, знакомых %(known_seen)s, "
        "новых %(new_listings)s, изменений цены %(price_changes)s, снято %(delisted)s",
        {**stats, "delisted": stats["delisted"] if stats["delisted"] is not None else "n/a"},
    )
    return stats
