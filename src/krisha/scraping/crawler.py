"""Краулер: страницы выдачи → id → детальные страницы → SQLite.

Возобновляемый: уже сохранённые id пропускаются, можно прерывать Ctrl+C
и запускать снова. Запуск: `python scripts/crawl.py --pages 50`.
"""

import logging

from krisha.config import SEARCH_URL
from krisha.db import count_listings, init_db, known_ids, upsert_listing
from krisha.scraping.client import PoliteClient
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
            html = client.get(url)
            if html is None:
                logger.error("Не удалось получить страницу выдачи %s — стоп", page)
                break

            ids = parse_listing_ids(html)
            fresh = [i for i in ids if i not in seen]
            logger.info("Стр. %s: %s объявлений, новых %s", page, len(ids), len(fresh))

            for lid in fresh:
                detail_url = f"https://krisha.kz/a/show/{lid}"
                detail_html = client.get(detail_url)
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

            if not has_next_page(html, page):
                logger.info("Дальше страниц нет — стоп")
                break

    logger.info("Готово: +%s новых, всего %s", new_count, count_listings())
    return new_count


# --- Сегментированный обход (этап «масштабирование базы») --------------------
# Один запрос `prodazha/kvartiry/almaty/` отдаёт лишь верхний срез выдачи
# (сортировка по свежести), поэтому добор упирается в дедупликацию. Чтобы
# достать всю выдачу, обходим много «срезов»: район × комнатность. Каждый срез
# отдаёт свой набор объявлений, а skip_known склеивает их без дублей.

ALMATY_DISTRICTS = [
    "bostandykskij",
    "almalinskij",
    "medeuskij",
    "auezovskij",
    "alatauskij",
    "turksibskij",
    "zhetysuskij",
    "nauryzbajskij",
]


def build_segments(districts=ALMATY_DISTRICTS, rooms=(1, 2, 3, 4, 5, 6)):
    """Список (имя, url) срезов: общая выдача + район + район×комнатность."""
    segments: list[tuple[str, str]] = [("all", SEARCH_URL)]
    base = "https://krisha.kz/prodazha/kvartiry"
    for d in districts:
        d_url = f"{base}/almaty-{d}-r-n/"
        segments.append((d, d_url))
        for r in rooms:
            segments.append((f"{d}/{r}k", f"{d_url}?das[live.rooms][]={r}"))
    return segments


def crawl_segments(
    segments,
    max_pages_per_segment: int = 500,
    max_listings: int | None = None,
    skip_known: bool = True,
    empty_page_stop: int = 3,
    fail_page_stop: int = 5,
    cursor: dict | None = None,
    cursor_path: str | None = None,
) -> int:
    """Обходит список срезов выдачи. Возвращает число новых записей.

    - empty_page_stop: подряд страниц без новых id → следующий срез.
    - fail_page_stop: подряд сетевых ошибок страницы выдачи → следующий срез
      (одна ошибка НЕ убивает весь прогон — в отличие от старого crawl()).
    Возобновляемый (skip_known): можно прерывать и перезапускать.
    """
    import json as _json
    import os as _os

    cursor = cursor if cursor is not None else {}

    def _save_cursor():
        if cursor_path:
            try:
                with open(cursor_path, "w") as _f:
                    _json.dump(cursor, _f)
            except OSError:
                pass

    init_db()
    seen = known_ids() if skip_known else set()
    logger.info("Старт сегментного обхода: в базе %s, срезов %s", len(seen), len(segments))
    new_count = 0
    # Предохранитель: если подряд столько запросов выдачи провалились (троттлинг
    # IP), прерываем весь раунд — внешний цикл переждёт блок и продолжит.
    abort_after = int(_os.environ.get("KRISHA_ABORT_AFTER_FAILS", "10"))
    global_fails = 0

    with PoliteClient() as client:
        for seg_name, seg_url in segments:
            if global_fails >= abort_after:
                logger.error("Подряд %s провалов выдачи — вероятный бан, прерываем раунд", global_fails)
                break
            if int(cursor.get(seg_name, 1)) >= 99999:
                continue  # срез уже исчерпан в прошлых запусках
            empties = 0
            fails = 0
            seg_new = 0
            start_page = int(cursor.get(seg_name, 1))
            for page in range(start_page, max_pages_per_segment + 1):
                sep = "&" if "?" in seg_url else "?"
                url = seg_url if page == 1 else f"{seg_url}{sep}page={page}"
                html = client.get(url)
                if html is None:
                    fails += 1
                    global_fails += 1
                    logger.warning("[%s] стр.%s не получена (%s/%s, global %s)", seg_name, page, fails, fail_page_stop, global_fails)
                    if global_fails >= abort_after:
                        break
                    if fails >= fail_page_stop:
                        logger.info("[%s] подряд ошибок %s — следующий срез", seg_name, fails)
                        break
                    continue
                fails = 0
                global_fails = 0
                cursor[seg_name] = page  # resume здесь при следующем запуске
                _save_cursor()

                ids = parse_listing_ids(html)
                # «Пустая» = страница вообще без карточек (конец выдачи), а НЕ
                # «все уже известны» — иначе на возобновлении мы бросали бы срез
                # на известном префиксе и не доходили до новых глубоких страниц.
                if ids:
                    empties = 0
                else:
                    empties += 1
                fresh = [i for i in ids if i not in seen]

                for lid in fresh:
                    detail_html = client.get(f"https://krisha.kz/a/show/{lid}")
                    if detail_html is None:
                        continue
                    listing = parse_detail(detail_html, f"https://krisha.kz/a/show/{lid}")
                    if listing is None:
                        continue
                    upsert_listing(listing)
                    seen.add(lid)
                    new_count += 1
                    seg_new += 1
                    if new_count % 50 == 0:
                        logger.info("Сохранено новых: %s (в базе %s)", new_count, count_listings())
                    if max_listings and new_count >= max_listings:
                        logger.info("Достигнут лимит %s — стоп", max_listings)
                        return new_count

                if empties >= empty_page_stop:
                    logger.info("[%s] %s стр. без карточек — срез исчерпан (+%s)", seg_name, empties, seg_new)
                    cursor[seg_name] = 99999
                    break
                if not has_next_page(html, page):
                    logger.info("[%s] страниц больше нет — срез исчерпан (+%s)", seg_name, seg_new)
                    cursor[seg_name] = 99999
                    break
            else:
                cursor[seg_name] = 99999  # дошли до max_pages_per_segment
            _save_cursor()
            logger.info("[%s] готов: +%s, всего новых %s, в базе %s", seg_name, seg_new, new_count, count_listings())

    logger.info("Сегментный обход завершён: +%s новых, в базе %s", new_count, count_listings())
    return new_count
