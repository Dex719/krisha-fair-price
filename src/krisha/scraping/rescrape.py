"""Этап 4 роадмапа: регулярный рескрейп → история цены и ликвидность.

Один проход (sweep) обходит выдачу и по карточкам (без детальных
страниц) обновляет базу:

- знакомое объявление: last_seen=now, цена изменилась → точка в price_history;
- новое объявление: детальная страница → upsert + стартовая точка истории;
- знакомое, но давно не виденное (DELIST_AFTER_DAYS): is_active=0 —
  считаем проданным/снятым, разница last_seen-first_seen = дни на рынке;
- знакомое активное с деталями старше refresh_stale_days: детальная
  страница докачивается повторно (issue #102) — иначе отредактированные
  продавцом площадь/этаж/описание/координаты не обновляются, пока лот жив.

Выдача шардируется по фильтрам «район × комнаты» (см. shard_urls):
общая выдача Алматы обрезается пагинацией и отдаёт только «популярные»
~7-8к объявлений, а 32 шарда суммарно покрывают почти все ~44к.

Запуск: `python scripts/rescrape.py` (по расписанию — ежедневно).
"""

import json
import logging
import os
import statistics
import time
from pathlib import Path

from krisha.config import ALMATY_DISTRICT_SLUGS, BASE_URL, DATA_DIR, ROOM_SHARDS
from krisha.db import (
    DB_PATH,
    _record_price_if_changed,
    get_conn,
    init_db,
    is_valid_price,
    known_ids,
    price_bounds_for,
    record_parse_anomaly,
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
#
# ВАЖНО (регрессия 14.07.2026 — из-за неё рескрейп продажи и аренды падал
# 13 дней подряд): здесь НЕЛЬЗЯ держать голые подстроки "captcha"/"recaptcha".
# krisha.kz печатает в подвале КАЖДОЙ нормальной страницы
# `<p class="g-recaptcha-policy">Этот сайт защищён сервисом reCAPTCHA…</p>`,
# поэтому такая подстрока находилась на любой живой выдаче: все 32 шарда
# объявлялись капчей, found_in_search=0, `--fail-empty` ронял воркфлоу, база
# не заливалась в релиз и протухала (а /api/health уходил в stale).
#
# Держим только маркеры самого челленджа, которых на обычной странице нет:
# виджет reCAPTCHA (именно `class="g-recaptcha"`, а не `g-recaptcha-policy`),
# скрипты/iframe челленджа и заглушки Cloudflare.
_ANTIBOT_SIGNS = (
    'class="g-recaptcha"',
    "recaptcha/api2",
    "grecaptcha.render",
    "data-sitekey",
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

# issue #152: доля активных, выше которой массовое снятие считаем сбоем, а не
# рынком. Нормальный delist — 1–4% в день. 30% означает, что выдача частично
# отдала пустые 200 без анти-бот маркеров: пометить их снятыми — испортить
# ликвидность и «дни на рынке» разом по всей базе, а откатить нечем.
MAX_DELIST_SHARE = 0.30

# Бюджет прохода по стенным часам. Раннер убивает джобу по timeout-minutes
# ЖЁСТКО, вместе с шагом заливки базы — то есть теряется вся ночная работа,
# включая полный обход выдачи и все собранные точки цен. Свой дедлайн
# останавливает мягко и оставляет время на upload.
DEFAULT_TIME_BUDGET_MIN = 320


def _now() -> float:
    """Монотонные часы отдельной функцией — чтобы тесты подменяли ЕЁ, а не
    time.monotonic глобально. И именно монотонные: настенные часы на раннере
    может дёрнуть NTP прямо посреди прохода."""
    return time.monotonic()


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
    """Страница-заглушка анти-бота/капчи вместо выдачи (см. _ANTIBOT_SIGNS)."""
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
    max_pages: int = 250,
    max_new_details: int = 300,
    db_path=DB_PATH,
    deal: str = "prodazha",
    refresh_stale_days: int = 30,
    max_refresh: int = 300,
    time_budget_min: float = DEFAULT_TIME_BUDGET_MIN,
) -> dict:
    """Один проход рескрейпа по всем шардам. Возвращает счётчики для лога/отчёта.

    max_pages — лимит страниц НА ОДИН шард (самый большой шард ~4к объявлений
    ≈ 200 страниц, так что 250 хватает с запасом).
    deal="arenda" — тот же проход по арендной выдаче (обычно в отдельную базу,
    см. RENT_DB_PATH), price = ₸/месяц.

    issue #102: карточка выдачи (found) обновляет только last_seen/цену — до
    этой правки площадь/этаж/описание/координаты, отредактированные продавцом,
    никогда не обновлялись, пока объявление живёт (детальная страница
    докачивалась только один раз, для новых id). refresh_stale_days/max_refresh
    — отдельная лимитированная очередь: активные лоты с детальными данными,
    у которых scraped_at старше refresh_stale_days, докачиваются повторно
    (самые старые — первыми), max_refresh за проход. max_refresh=0 выключает.
    """
    init_db(db_path)
    # Контракт на цену зависит от типа сделки: продажа — ₸ за квартиру,
    # аренда — ₸/месяц. Пока границы были захардкожены на продажу, арендный
    # проход отбраковывал каждую цену и krisha_rent.db не обновлялась вовсе.
    bounds = price_bounds_for(deal)
    seen_in_db = known_ids(db_path)
    with get_conn(db_path) as conn:
        active_in_db = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1"
        ).fetchone()[0]
    found: dict[int, int | None] = {}
    failed_shards: list[str] = []
    banned = False
    banned_phase: str | None = None
    deadline = _now() + time_budget_min * 60.0
    time_budget_hit = False

    def out_of_time() -> bool:
        return _now() >= deadline

    with PoliteClient() as client:
        shards = shard_urls(deal)
        for i, (label, base_url) in enumerate(shards):
            if out_of_time():
                # Недообойдённые шарды — в failed_shards: их объявления не
                # получили last_seen, и без этой пометки они уехали бы в
                # delisted (issue #96).
                time_budget_hit = True
                remaining = [lbl for lbl, _ in shards[i:]]
                failed_shards.extend(remaining)
                logger.error(
                    "Бюджет времени (%s мин) исчерпан на фазе выдачи — не обойдено шардов: %s",
                    time_budget_min, len(remaining),
                )
                break
            try:
                if not _sweep_shard(client, label, base_url, max_pages, found):
                    failed_shards.append(label)
            except BanDetected as exc:
                banned_phase = "search"
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
            record_sighting(
                lid, f"https://krisha.kz/a/show/{lid}", found[lid], db_path,
                price_bounds=bounds,
            )

        # Очередь detail fetch — самые старые ещё не докачанные лоты первыми
        # (title IS NULL — сентинел «есть только sighting, детали ещё нет»),
        # а не «что нашли в этом проходе первым» (смещение по шардам).
        # is_active = 1 обязателен: лот может получить sighting и быть снят
        # с продажи (is_active=0) раньше, чем очередь до него дойдёт — без
        # фильтра такой «труп» навсегда остаётся с title IS NULL (детальная
        # страница отдаёт 404 → parse_detail() -> None → title не
        # заполняется) и застревает в голове FIFO, съедая бюджет докачки на
        # каждом проходе.
        # issue #152: порядок был FIFO по first_seen, а first_seen внутри
        # прохода = порядок обхода шардов = алфавит районов. Алатауский идёт
        # первым и выедал весь бюджет: на момент правки у него 7094 лота с
        # деталями при медиане 631 тыс ₸/м², а у Бостандыкского — 1672 при
        # 1015 тыс, у Медеуского — 639 при 996 тыс. Модель оказалась хуже
        # всего обучена там, где квадратный метр дороже всего.
        # Круговой обход по «район × комнаты» даёт каждому сегменту равную
        # долю бюджета в каждом раунде, внутри сегмента — свежие первыми
        # (id на krisha монотонен по времени подачи, а после массового
        # бэкфилла first_seen у тысяч лотов совпадает до секунд и сортировать
        # по нему бессмысленно).
        with get_conn(db_path) as conn:
            backlog_ids = [
                r[0]
                for r in conn.execute(
                    """
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY COALESCE(district, '?'), COALESCE(rooms, -1)
                            ORDER BY id DESC
                        ) AS rn
                        FROM listings WHERE title IS NULL AND is_active = 1
                    ) ORDER BY rn, id DESC
                    """
                ).fetchall()
            ]
        queue_size_before = len(backlog_ids)
        to_fetch = [] if banned else backlog_ids[:max_new_details]
        new_count = 0
        for lid in to_fetch:
            if out_of_time():
                # Проверяем перед КАЖДЫМ запросом, а не раз в шард: один шард
                # это до 250 страниц ≈ 13 минут, промах на такую величину
                # съедает весь запас на заливку базы.
                time_budget_hit = True
                logger.error("Бюджет времени исчерпан на докачке деталей — мягкий стоп")
                break
            try:
                detail_html = client.get(f"https://krisha.kz/a/show/{lid}")
            except BanDetected as exc:
                logger.critical("%s — прерываем докачку деталей", exc)
                if not banned:
                    banned_phase = "details"
                    _alert_ban(exc)
                banned = True
                break
            listing = parse_detail(detail_html, f"https://krisha.kz/a/show/{lid}") if detail_html else None
            if listing is not None:
                upsert_listing(listing, db_path, price_bounds=bounds)
                new_count += 1
        queue_size_after = queue_size_before - new_count

        # issue #102: отдельная лимитированная очередь — активные лоты,
        # уже имеющие детали (title IS NOT NULL), но не докачанные дольше
        # refresh_stale_days. Без этого отредактированные продавцом
        # площадь/этаж/описание/координаты никогда не подтягиваются, пока
        # объявление живёт — рескрейп по карточке обновляет только цену.
        # Самые давно не докачанные — первыми (ORDER BY scraped_at ASC).
        refresh_queue_size = 0
        refreshed_count = 0
        if not banned and max_refresh > 0:
            with get_conn(db_path) as conn:
                refresh_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM listings WHERE title IS NOT NULL AND is_active = 1 "
                        "AND julianday('now') - julianday(scraped_at) > ? "
                        "ORDER BY scraped_at ASC LIMIT ?",
                        (refresh_stale_days, max_refresh),
                    ).fetchall()
                ]
            refresh_queue_size = len(refresh_ids)
            for lid in refresh_ids:
                if out_of_time():
                    time_budget_hit = True
                    logger.error("Бюджет времени исчерпан на обновлении деталей — мягкий стоп")
                    break
                try:
                    detail_html = client.get(f"https://krisha.kz/a/show/{lid}")
                except BanDetected as exc:
                    logger.critical("%s — прерываем обновление устаревших деталей", exc)
                    if not banned:
                        banned_phase = "details"
                        _alert_ban(exc)
                    banned = True
                    break
                listing = (
                    parse_detail(detail_html, f"https://krisha.kz/a/show/{lid}")
                    if detail_html
                    else None
                )
                if listing is not None:
                    upsert_listing(listing, db_path, price_bounds=bounds)
                    refreshed_count += 1

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
            if price is None:
                continue
            # issue #103: карточка выдачи не проходит парсер детальной
            # страницы (нет upsert_listing/_validate_and_quarantine) — тот же
            # data-contract на цену нужен и здесь, иначе битый парс карточки
            # (не upsert) свободно пишется в listings.price/price_history.
            if not is_valid_price(price, bounds):
                record_parse_anomaly(conn, lid, "price", "out_of_range", price)
                continue
            if _record_price_if_changed(conn, lid, price):
                conn.execute("UPDATE listings SET price = ? WHERE id = ?", (price, lid))
                price_changes += 1

        # Помечаем снятые только после полного покрытия: если хоть один шард
        # не дообошли (блокировка/сеть), его объявления не получили last_seen,
        # и delisted был бы ложным.
        delisted_count: int | None = 0
        delist_blocked = False
        if not failed_shards:
            where = (
                "is_active = 1 "
                f"AND julianday('now') - julianday(last_seen) > {DELIST_AFTER_DAYS}"
            )
            candidates = conn.execute(f"SELECT COUNT(*) FROM listings WHERE {where}").fetchone()[0]
            # issue #152: гард на массовое снятие. Нормальный delist — 1–4% в
            # день; доля в разы выше означает, что выдача частично отдала
            # пустые 200 без анти-бот маркеров (шард формально «покрыт»).
            # Пометить их снятыми — испортить ликвидность и «дни на рынке»
            # разом по всей базе, а отката нет. Смотрим именно на ДОЛЮ:
            # после разгона активных станет ~40k, и абсолютные пороги соврут.
            share = candidates / active_in_db if active_in_db else 0.0
            if active_in_db >= MIN_ACTIVE_IN_DB_FOR_CHECK and share > MAX_DELIST_SHARE:
                delist_blocked = True
                delisted_count = None
                logger.error(
                    "Кандидатов на снятие %s из %s активных (%.0f%%, порог %.0f%%) — "
                    "это не рынок, а сбой сбора. Пометку delisted пропускаем",
                    candidates, active_in_db, share * 100, MAX_DELIST_SHARE * 100,
                )
            else:
                delisted = conn.execute(
                    "UPDATE listings SET is_active = 0, delisted_at = datetime('now') "
                    f"WHERE {where} RETURNING id",
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
        # ВНИМАНИЕ на семантику (легко перепутать):
        # discovered_new — сколько id впервые увидели в выдаче за этот проход;
        # details_fetched (= legacy new_listings) — сколько детальных страниц
        # реально докачали. Второе берётся из ОБЩЕЙ очереди backlog'а, куда
        # входят и находки прошлых проходов, и упирается в max_new_details,
        # поэтому «новых» в отчётах надо показывать именно discovered_new:
        # раньше проход, который ничего не нашёл, но дочистил хвост очереди,
        # рапортовал «новых 300».
        "discovered_new": len(new_ids),
        "details_fetched": new_count,
        "new_listings": new_count,  # legacy-ключ: старые summary-JSON и тесты
        "price_changes": price_changes,
        "delisted": delisted_count,
        "failed_shards": failed_shards,
        "active_in_db_before": active_in_db,
        "parse_rate_median_7": parse_rate_median,
        "suspicious": suspicious,
        # issue #101: early-abort по серии 403 (см. BanDetected) — проход
        # прерван до конца шардов/очереди докачки, а не тихо неполный.
        "banned": banned,
        # issue #152: бан на ДЕТАЛЯХ не обесценивает уже собранную выдачу —
        # цены и last_seen валидны, базу нужно залить. Бан на ВЫДАЧЕ означает
        # неполное покрытие, заливать нельзя. Раньше одна серия 403 на
        # деталях выбрасывала целиком успешный ночной обход.
        "banned_phase": banned_phase,
        # Мягкий стоп по своему дедлайну вместо жёсткого kill раннера,
        # который убивает джобу вместе с шагом заливки базы.
        "time_budget_hit": time_budget_hit,
        "delist_blocked": delist_blocked,
        # getattr: в тестах клиент подменяется двойником без телеметрии
        "client": getattr(client, "stats", {}),
        # issue #127: сколько лотов ждут detail fetch (sighting есть, деталей
        # ещё нет) — растущая очередь сигналит, что max_new_details мал
        # относительно притока новых объявлений.
        "detail_queue_before": queue_size_before,
        "detail_queue_after": queue_size_after,
        # issue #102: сколько активных лотов с устаревшими деталями
        # (>refresh_stale_days с последнего scraped_at) стояло в очереди на
        # этот проход, и сколько реально докачали в рамках max_refresh.
        "stale_refresh_queue": refresh_queue_size,
        "stale_refreshed": refreshed_count,
    }
    logger.info(
        "Рескрейп: в выдаче %(found_in_search)s, знакомых %(known_seen)s, "
        "новых %(discovered_new)s (докачано деталей %(details_fetched)s), "
        "изменений цены %(price_changes)s, снято %(delisted)s, "
        "очередь деталей %(detail_queue_after)s, обновлено устаревших %(stale_refreshed)s",
        {**stats, "delisted": stats["delisted"] if stats["delisted"] is not None else "n/a"},
    )
    return stats
