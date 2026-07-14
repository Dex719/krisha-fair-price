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
import os
import statistics
from pathlib import Path

from krisha.config import ALMATY_DISTRICT_SLUGS, BASE_URL, DATA_DIR, ROOM_SHARDS
from krisha.db import (
    DB_PATH,
    _record_price_if_changed,
    get_conn,
    init_db,
    known_ids,
    record_sighting,
    upsert_listing,
)
from krisha.monitoring import ADMIN_CHAT_ENV
from krisha.scraping.client import BanDetected, PoliteClient
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

# История found_in_search последних проходов — вспомогательный сигнал ТОЛЬКО
# для локальных/долгоживущих запусков. В GitHub Actions раннер каждый раз
# чистый и файл в .gitignore, так что в проде история никогда не наберёт
# порог и не сработает — это самостоятельно не проверка, а бонус поверх
# основной, которая опирается на БД (см. ACTIVE_IN_DB_DROP_RATIO ниже).
PARSE_RATE_HISTORY_LEN = 7
PARSE_RATE_DROP_RATIO = 0.5  # алерт, если текущий проход < 50% медианы истории

# Основной прод-детект (issue #97, ревью Декса на PR #125): в отличие от
# файла истории, состояние БД приходит с раннером (скачивается перед
# рескрейпом) — сравниваем found_in_search с числом активных объявлений
# в БД ДО этого прохода. Порог count'а — если самих активных совсем мало
# (холодная/тестовая БД), сравнение ничего не даёт и его пропускаем.
ACTIVE_IN_DB_DROP_RATIO = 0.5
MIN_ACTIVE_IN_DB_FOR_CHECK = 100


def _alert_ban(exc: BanDetected) -> None:
    """Телеграм-алерт админу при early-abort по бану (issue #101).

    Тот же паттерн ленивого импорта `tg_call`, что в monitoring.py/usage.py —
    `bot.py` тянет более тяжёлые зависимости, не грузим их, если чат не задан.
    """
    chat_id = os.environ.get(ADMIN_CHAT_ENV)
    if not chat_id:
        logger.info("%s не задан — алерт о бане не отправлен", ADMIN_CHAT_ENV)
        return
    from krisha.bot import tg_call

    try:
        tg_call(
            "sendMessage",
            chat_id=int(chat_id),
            text=f"🚫 Krisha rescrape: {exc}\nПроход прерван досрочно (early-abort).",
        )
    except Exception as e:  # noqa: BLE001 — алерт не должен уронить сам проход
        logger.warning("Не удалось отправить алерт о бане: %s", e)


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
    with get_conn(db_path) as conn:
        active_in_db = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1"
        ).fetchone()[0]
    found: dict[int, int | None] = {}
    failed_shards: list[str] = []
    banned = False

    with PoliteClient() as client:
        for label, base_url in shard_urls(deal):
            try:
                if not _sweep_shard(client, label, base_url, max_pages, found):
                    failed_shards.append(label)
            except BanDetected as exc:
                logger.critical(
                    "%s — прерываем проход на шарде «%s», остальные шарды не обходим",
                    exc,
                    label,
                )
                failed_shards.append(label)
                banned = True
                _alert_ban(exc)
                break

        # issue #127: раньше в базу шли только первые max_new_details новых id
        # (в порядке обхода шардов) — остальные найденные теряли даже
        # first_seen. Теперь sighting пишем для ВСЕХ новых id сразу (дёшево,
        # без похода на детальную страницу); полный detail fetch остаётся
        # отдельной лимитированной очередью.
        new_ids = [lid for lid in found if lid not in seen_in_db]
        for lid in new_ids:
            record_sighting(lid, f"https://krisha.kz/a/show/{lid}", found[lid], db_path)

        # Очередь detail fetch — самые старые ещё не докачанные лоты первыми
        # (title IS NULL — сентинел «есть только sighting, детали ещё нет»),
        # а не «что нашли в этом проходе первым» (смещение по шардам).
        # is_active = 1 обязателен: лот может получить sighting и быть снят
        # с продажи (is_active=0) раньше, чем очередь до него дойдёт — без
        # фильтра такой «труп» навсегда остаётся с title IS NULL (детальная
        # страница отдаёт 404 → parse_detail() -> None → title не
        # заполняется) и застревает в голове FIFO, съедая бюджет докачки на
        # каждом проходе.
        with get_conn(db_path) as conn:
            backlog_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM listings WHERE title IS NULL AND is_active = 1 "
                    "ORDER BY first_seen ASC"
                ).fetchall()
            ]
        queue_size_before = len(backlog_ids)
        to_fetch = [] if banned else backlog_ids[:max_new_details]
        new_count = 0
        for lid in to_fetch:
            try:
                detail_html = client.get(f"https://krisha.kz/a/show/{lid}")
            except BanDetected as exc:
                logger.critical("%s — прерываем докачку деталей", exc)
                if not banned:
                    _alert_ban(exc)
                banned = True
                break
            listing = parse_detail(detail_html, f"https://krisha.kz/a/show/{lid}") if detail_html else None
            if listing is not None:
                upsert_listing(listing, db_path)
                new_count += 1
        queue_size_after = queue_size_before - new_count

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

    # Тихая деградация (сервер отвечает, шарды формально покрыты, но
    # объявлений в разы меньше обычного) не ловится через failed_shards —
    # сравниваем found_in_search с базлайном (issue #97).
    #
    # Основной прод-детект (ревью Декса на PR #125): количество активных
    # объявлений в БД ДО этого прохода — БД приходит артефактом с раннером,
    # так что работает и на чистой машине без своего состояния. Порог не
    # проверяем, если самих активных в БД слишком мало (холодная/тестовая
    # база — сравнение только шумит).
    suspicious_db = (
        active_in_db >= MIN_ACTIVE_IN_DB_FOR_CHECK
        and len(found) < active_in_db * ACTIVE_IN_DB_DROP_RATIO
    )
    if suspicious_db:
        logger.error(
            "Parse-rate просел: в выдаче %s против %s активных в БД до прохода "
            "(порог %.0f%%) — проход помечен подозрительным",
            len(found),
            active_in_db,
            ACTIVE_IN_DB_DROP_RATIO * 100,
        )

    # Доп. сигнал по локальной истории проходов — файл в .gitignore, на
    # чистом раннере GitHub Actions недоступен, поэтому это дополнение
    # только для локальных/долгоживущих запусков, не основная защита.
    history = _load_history(deal)
    parse_rate_median = (
        statistics.median(history[-PARSE_RATE_HISTORY_LEN:]) if len(history) >= 3 else None
    )
    suspicious_history = (
        parse_rate_median is not None and len(found) < parse_rate_median * PARSE_RATE_DROP_RATIO
    )
    if suspicious_history and not suspicious_db:
        logger.error(
            "Parse-rate просел: в выдаче %s против медианы %s последних проходов "
            "(порог %.0f%%) — проход помечен подозрительным",
            len(found),
            parse_rate_median,
            PARSE_RATE_DROP_RATIO * 100,
        )
    history.append(len(found))
    _save_history(deal, history)

    suspicious = suspicious_db or suspicious_history

    stats = {
        "found_in_search": len(found),
        "known_seen": len(known_seen),
        "new_listings": new_count,
        "price_changes": price_changes,
        "delisted": delisted_count,
        "failed_shards": failed_shards,
        "active_in_db_before": active_in_db,
        "parse_rate_median_7": parse_rate_median,
        "suspicious": suspicious,
        # issue #101: early-abort по серии 403 (см. BanDetected) — проход
        # прерван до конца шардов/очереди докачки, а не тихо неполный.
        "banned": banned,
        # issue #127: сколько лотов ждут detail fetch (sighting есть, деталей
        # ещё нет) — растущая очередь сигналит, что max_new_details мал
        # относительно притока новых объявлений.
        "detail_queue_before": queue_size_before,
        "detail_queue_after": queue_size_after,
    }
    logger.info(
        "Рескрейп: в выдаче %(found_in_search)s, знакомых %(known_seen)s, "
        "новых %(new_listings)s, изменений цены %(price_changes)s, снято %(delisted)s, "
        "очередь деталей %(detail_queue_after)s",
        {**stats, "delisted": stats["delisted"] if stats["delisted"] is not None else "n/a"},
    )
    return stats
