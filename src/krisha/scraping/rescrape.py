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

from krisha.config import (
    ALMATY_DISTRICT_SLUGS,
    BASE_URL,
    DATA_DIR,
    REQUEST_DELAY_RANGE,
    ROOM_SHARDS,
)
from krisha.db import (
    DB_PATH,
    _record_price_if_changed,
    advance_shard_cursor,
    advance_sweep_pass_seq,
    consecutive_bans,
    get_conn,
    init_db,
    is_valid_price,
    known_ids,
    last_known_shard_stock,
    last_sweep_mode,
    price_bounds_for,
    recent_sweep_runs,
    record_consecutive_bans,
    record_listing_shards,
    record_parse_anomaly,
    record_sighting,
    record_sweep_run,
    record_sweep_shard_stats,
    shard_backlog_count,
    shard_backlog_window,
    shard_cursors,
    unattributed_backlog_count,
    upsert_listing,
    zero_quota_streak,
)
from krisha.monitoring import ADMIN_CHAT_ENV
from krisha.scraping.client import BanDetected, PoliteClient
from krisha.scraping.detail_parser import parse_detail
from krisha.scraping.listing_parser import has_next_page, parse_listing_prices
from krisha.scraping.pass_plan import (
    BAN_ROLLBACK_STREAK,
    STEADY_MODE,
    choose_mode,
    estimate_new_inflow,
    estimate_timings,
    fit_detail_caps,
    mode_by_name,
    plan_pass,
)
from krisha.scraping.shard_plan import (
    largest_remainder_quotas,
    redistribute_leftover,
    rotated,
    rotation_offset,
    shard_district,
)
from krisha.validity import MAX_TEST_TVD, total_variation_distance

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

# issue #156: разрыв в наблюдении, после которого проход считается
# восстановительным, а не обычным.
#
# 2.5 суток, а не «чуть больше суток»: при суточной каденции ОДИН упавший
# крон даёт разрыв в 2 дня, и это всё ещё обычный приток за два дня, а не
# когорта бэкфилла. Помечать его когортой — молча выкинуть двое суток
# нормальных данных из обучения и статистики. Два упавших крона подряд
# (разрыв 3 дня) — уже да.
#
# Разрыв НЕ хранится отдельным полем, а выводится из самой базы:
# MAX(last_seen) — последний момент, когда мы хоть что-то наблюдали живым.
# Отдельное поле пришлось бы поддерживать в актуальном состоянии, и оно
# разъехалось бы при откате базы из старого релиза; выведенное значение
# верно всегда и именно для той базы, с которой проход реально работает.
RECOVERY_GAP_DAYS = 2.5

# Сколько суток после НЕПОЛНОГО восстановительного прохода считаем, что мы
# всё ещё разгребаем его когорту. Применяется только если тот проход не
# добрал выдачу (упал шард, кончился бюджет времени) — при полном обходе
# вся волна получает first_seen сразу, и растягивать пометку значит
# записывать в когорту честную органику следующих дней.
GAP_COHORT_GRACE_DAYS = 3

# issue #168: сколько проходов подряд шард может иметь непустой backlog при
# нулевой квоте, прежде чем это станет событием, а не тишиной. Один-два
# прохода — обычное колебание largest remainder (мелкий шард то получает
# +1 по дробной части, то нет); три подряд — шаблон: шард хронически
# усечён (сток NULL → строки в last_known_shard_stock нет → квота 0) и
# backlog растёт незаметно для TVD порции (шарда нет ни в факте, ни в
# стоке). Флаг уезжает в итоги прохода (stats["starved_shards"]) и дальше
# по готовому пути: summary-json → вердикт утреннего отчёта админу.
STARVED_SHARD_STREAK = 3


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


def _alert_ban_rollback(streak: int) -> None:
    """Алерт админу при откате разгона по серии банов (issue #152).

    Шлём один раз, на фронте серии (streak достиг порога): пока откат
    активен, каждый проход несёт stats["ban_rollback"], и повторный алерт был
    бы спамом; новая серия после чистого прохода — новый фронт, новый алерт.
    """
    chat_id = os.environ.get(ADMIN_CHAT_ENV)
    if not chat_id:
        logger.info("%s не задан — алерт об откате разгона не отправлен", ADMIN_CHAT_ENV)
        return
    from krisha.bot import tg_call

    try:
        tg_call(
            "sendMessage",
            chat_id=int(chat_id),
            text=(
                f"🐌 Krisha rescrape: {streak} прохода подряд с баном — разгон (#152) "
                "откачен к steady-задержкам (2.0–4.0 с) до первого чистого прохода. "
                "Разгон, который поймал бан и продолжает разгоняться, хуже отсутствия "
                "разгона: потеряем и день, и IP."
            ),
        )
    except Exception as e:  # noqa: BLE001 — алерт не должен уронить сам проход
        logger.warning("Не удалось отправить алерт об откате разгона: %s", e)


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
) -> tuple[dict[int, int | None], bool, BanDetected | None, int]:
    """Обходит пагинацию одного шарда: ({id: цена}, покрыт_ли, бан, страниц).

    covered=False, если шард не покрыт: страница не загрузилась
    (сеть/блокировка), пойман BanDetected, страница похожа на анти-бот/капчу,
    первая страница дала 0 валидных id (сервер отдал 200, но не выдачу —
    изменённая вёрстка и т.п.), или выдача шарда глубже max_pages (гард-
    остаток #152: усечение тоже непокрытие). Пустой/анти-бот/усечённый шард
    НЕ считается покрытием — иначе живые объявления рискуют быть ложно
    помечены delisted (issue #96). Уже загруженные страницы при этом НЕ
    выбрасываются: их id честно увидены в выдаче и получают
    sighting/last_seen, непокрытие лишь запрещает delist и замер стока по
    этому шарду в этом проходе. Бан возвращается третьим элементом, а не
    исключением: частично загруженные страницы переживают его так же, как
    при сетевом сбое (до #166 они выживали в общем словаре — поведение
    выровнено по ревью); решение об остановке прохода принимает sweep().

    Четвёртый элемент — сколько страниц фактически запрошено (issue #152:
    фактическая цена фазы выдачи для оценки прохода и записи таймингов в
    sweep_runs; на упавшем шарде — честно сделанные запросы до обрыва).

    issue #166: шард возвращает СВОИХ найденных id отдельно, а не дописывает
    в общий котел — состав шарда это сток выдачи для квоты докачки и источник
    атрибуции id → шард (у лота без деталей district/rooms неизвестны,
    а «круговая» очередь #152 по колонкам listings на них вырождалась).
    """
    found: dict[int, int | None] = {}
    pages_done = 0
    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}&page={page}"
        try:
            html = client.get(url)
        except BanDetected as exc:
            return found, False, exc, pages_done
        pages_done += 1
        if html is None:
            logger.error("Шард «%s»: стр. %s не загрузилась — стоп шарда", label, page)
            return found, False, None, pages_done
        if page == 1 and _looks_like_antibot(html):
            logger.error("Шард «%s»: похоже на анти-бот/капча страницу — стоп шарда", label)
            return found, False, None, pages_done
        page_prices = parse_listing_prices(html)
        if page == 1 and not page_prices:
            logger.warning(
                "Шард «%s»: 0 валидных id на первой странице — подозрительно, шард не покрыт",
                label,
            )
            return found, False, None, pages_done
        found.update(page_prices)
        if not has_next_page(html, page):
            break
    else:
        # Вышли по max_pages, не встретив конец пагинации: выдача шарда
        # глубже лимита — покрытие неполное. Лоты за срезом не получат
        # sighting/атрибуцию, а delist по шарду будет ложным (гард-остаток
        # #152). При штатном --pages=250 (с запасом над ~100 страницами
        # самого большого шарда) ветка спит; проснётся, если лимит ужать.
        logger.error(
            "Шард «%s»: выдача глубже max_pages=%s — покрытие неполное, delist запрещён",
            label, max_pages,
        )
        return found, False, None, pages_done
    logger.info("Шард «%s»: %s объявлений в выдаче", label, len(found))
    return found, True, None, pages_done


def sweep(
    max_pages: int = 250,
    max_new_details: int | None = None,
    db_path=DB_PATH,
    deal: str = "prodazha",
    refresh_stale_days: int | None = None,
    max_refresh: int | None = None,
    time_budget_min: float = DEFAULT_TIME_BUDGET_MIN,
    mode: str = "auto",
) -> dict:
    """Один проход рескрейпа по всем шардам. Возвращает счётчики для лога/отчёта.

    max_pages — лимит страниц НА ОДИН шард (самый большой шард ~4к объявлений
    ≈ 200 страниц, так что 250 хватает с запасом).
    deal="arenda" — тот же проход по арендной выдаче (обычно в отдельную базу,
    см. RENT_DB_PATH), price = ₸/месяц.

    issue #152: параметры интенсивности (max_new_details / max_refresh /
    refresh_stale_days / паузы клиента) по умолчанию (None) берутся из
    РЕЖИМА прохода, который выбирается по состоянию базы в его начале
    (pass_plan.choose_mode по backlog'у с гистерезисом), а не по флагу
    запуска и не по календарю: drain разгребает backlog (4500 новых/сутки
    при паузах 1.5–3.0 с), steady поддерживает покрытие. Явные значения
    аргументов и env KRISHA_DELAY_MIN/MAX — оверрайды поверх пресета
    (ручные/диагностические запуски); mode="drain"/"steady" фиксирует
    пресет в обход backlog'а. Решение (режим, причина, эффективные потолки)
    пишется в итоги прохода. Потолок докачки перед фазой деталей подрезается
    под фактический остаток бюджета и потолок вежливости (pass_plan.
    fit_detail_caps) — проход, не влезающий по плану, урезает потолок сам,
    а не упирается в дедлайн на середине. Два подряд прохода с баном —
    возврат к steady-задержкам (pass_plan.BAN_ROLLBACK_STREAK).

    issue #102: карточка выдачи (found) обновляет только last_seen/цену — до
    этой правки площадь/этаж/описание/координаты, отредактированные продавцом,
    никогда не обновлялись, пока объявление живёт (детальная страница
    докачивалась только один раз, для новых id). refresh_stale_days/max_refresh
    — отдельная лимитированная очередь: активные лоты с детальными данными,
    у которых scraped_at старше refresh_stale_days, докачиваются повторно
    (самые старые — первыми), max_refresh за проход. max_refresh=0 выключает.

    issue #166: очередь новых лотов шардирована «район × комнаты». Дневной
    лимит max_new_details раскладывается квотами пропорционально фактическому
    стоку выдачи каждого шарда (а не отдаётся первым районам алфавита и не
    режется по свежести id), у каждого шарда — свой круговой курсор в
    shard_cursors, недобор по шарду не передаётся соседям. Порядок обхода
    шардов ротируется от прохода к проходу, план/факт по шардам пишется в
    sweep_shard_stats. Число запросов за проход не меняется: те же страницы
    выдачи + тот же потолок докачки, меняется только ПОРЯДОК.

    issue #168: номер прохода инкрементируется в начале (отдельной
    транзакцией), чтобы жёстко убитый раннер не залипал на одном смещении
    ротации; шард с непустым backlog'ом и нулевой квотой STARVED_SHARD_STREAK
    проходов подряд поднимается как событие (stats["starved_shards"]) —
    оба хвоста того же класса, что и #166: сбор молча останавливается, и
    узнаём мы об этом через две недели из model_meta.
    """
    init_db(db_path)
    # Контракт на цену зависит от типа сделки: продажа — ₸ за квартиру,
    # аренда — ₸/месяц. Пока границы были захардкожены на продажу, арендный
    # проход отбраковывал каждую цену и krisha_rent.db не обновлялась вовсе.
    bounds = price_bounds_for(deal)
    seen_in_db = known_ids(db_path)

    # --- issue #152: режим прохода по состоянию базы, до первого запроса ---
    # Решение принимается в начале прохода (паузы влияют и на фазу выдачи)
    # и пишется в итоги: из лога должно быть видно, в каком режиме шёл
    # проход и почему. backlog здесь — ДО сайтингов этого прохода: режим
    # описывает состояние базы, с которым проход стартовал.
    with get_conn(db_path) as conn:
        backlog_at_start = shard_backlog_count(conn)
        prev_mode = last_sweep_mode(conn, deal)
        ban_streak_at_start = consecutive_bans(conn, deal)
    if mode != "auto":
        preset = mode_by_name(mode)
        mode_reason = f"пресет задан явно (mode={mode}), backlog {backlog_at_start} проигнорирован"
    else:
        preset, mode_reason = choose_mode(backlog_at_start, prev_mode)
    # Разгребание завершилось на прошлом проходе: прежний режим drain,
    # текущий steady при авто-выборе. Сигнал для отчёта: на полном покрытии
    # пересчитывается статистика дедупликации (scripts/dedup_stats.py) —
    # на текущем составе она одна, на полном будет другой.
    drain_completed = (
        mode == "auto" and prev_mode == "drain" and preset.name == "steady"
    )
    want_new = preset.max_new if max_new_details is None else max_new_details
    want_refresh = preset.max_refresh if max_refresh is None else max_refresh
    eff_stale_days = (
        preset.refresh_stale_days if refresh_stale_days is None else refresh_stale_days
    )
    # Паузы: явный env-оверрайд (KRISHA_DELAY_MIN/MAX — их выставляет воркфлоу
    # или оператор ручного прогона) побеждает пресет; иначе — паузы режима.
    # Пустая строка (плейсхолдер input'а воркфлоу без значения) — НЕ оверрайд.
    explicit_delay = any(
        os.environ.get(name, "").strip()
        for name in ("KRISHA_DELAY_MIN", "KRISHA_DELAY_MAX")
    )
    delay_range = REQUEST_DELAY_RANGE if explicit_delay else preset.delay_range
    # Откат по бану (issue #152): два подряд прохода с BanDetected — темп
    # возвращается к steady-задержкам независимо от режима. Потолки явно не
    # трогаем: их урежет fit_detail_caps по бюджету времени с новыми паузами
    # — одна точка правды про «сколько влезает».
    ban_rollback = ban_streak_at_start >= BAN_ROLLBACK_STREAK
    if ban_rollback and delay_range != STEADY_MODE.delay_range:
        logger.error(
            "Откат по бану: %s подряд прохода с BanDetected — паузы возвращены "
            "к steady %.1f–%.1f с до первого чистого прохода",
            ban_streak_at_start, *STEADY_MODE.delay_range,
        )
        delay_range = STEADY_MODE.delay_range
    logger.info(
        "Режим прохода: %s (%s); паузы %.1f–%.1f с%s; потолки: новых %s, "
        "refresh %s (старше %s дн.)",
        preset.name, mode_reason, delay_range[0], delay_range[1],
        ", откат по банам" if ban_rollback else "",
        want_new, want_refresh, eff_stale_days,
    )
    if drain_completed:
        logger.warning(
            "Backlog разобран (режим drain → steady): на полном покрытии "
            "пересчитайте дедупликацию по fingerprint — scripts/dedup_stats.py"
        )

    # Оценка прохода до его начала (issue #152): тайминги фаз — фактические,
    # из истории sweep_runs (после ~3 проходов с этой схемой), до того —
    # откалиброванные по прод-телеметрии фолбэки. Проход, который по плану
    # не влезает в бюджет, обязан урезать потолок сам — это делает
    # fit_detail_caps после фазы выдачи с фактической её ценой.
    runs_history = recent_sweep_runs(limit=7, deal=deal, db_path=db_path)
    timings = estimate_timings(runs_history)
    with get_conn(db_path) as conn:
        # Бюджет тратят реальные очереди, а не потолки: очередь refresh при
        # steady-пороге 45 дней может быть меньше пресетных 1200 — оценивать
        # проход по потолку значило бы систематически завышать план.
        refresh_queue_at_start = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE title IS NOT NULL AND is_active = 1 "
            "AND julianday('now') - julianday(scraped_at) > ?",
            (eff_stale_days,),
        ).fetchone()[0]
    want_refresh_q = min(want_refresh, refresh_queue_at_start)
    # Очередь новых к фазе деталей = старый backlog + СВЕЖИЕ сайтинги этого
    # прохода (приток из истории) — одним backlog'ом план занижался бы на
    # органику, а на первом проходе по базе обнулялся бы вовсе.
    want_new_est = min(want_new, backlog_at_start + estimate_new_inflow(runs_history))
    plan_estimate = plan_pass(
        want_new_est,
        want_refresh_q,
        timings,
        time_budget_min,
        delay_range,
    )
    logger.info(
        "План прохода: ~%s запросов ≈ %.0f мин (выдача ~%s стр ≈ %.0f мин, "
        "детали %s ≈ %.0f мин; навершие %.2f/%.2f с, замеров %s) при бюджете "
        "%.0f мин, темп %.2f rps — %s",
        plan_estimate.est_requests,
        plan_estimate.est_seconds / 60,
        plan_estimate.est_search_pages,
        timings.search_pages * (sum(delay_range) / 2 + timings.overhead_search) / 60,
        plan_estimate.est_detail_requests,
        plan_estimate.est_detail_requests * (sum(delay_range) / 2 + timings.overhead_detail) / 60,
        timings.overhead_search, timings.overhead_detail, timings.samples,
        time_budget_min,
        plan_estimate.mean_rps,
        "влезает" if plan_estimate.fits else "НЕ ВЛЕЗАЕТ — потолок будет урезан заранее",
    )
    with get_conn(db_path) as conn:
        active_in_db = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1"
        ).fetchone()[0]
        # issue #156: сколько прошло с последнего момента, когда мы вообще
        # что-либо наблюдали живым. На здоровом ежедневном расписании это ~1
        # день; больше — значит был перерыв, и всё, что этот проход насчитает
        # «новым» и «снятым», надо трактовать иначе.
        #
        # Приток: лоты, которых мы не видели, — это НЕ приток рынка, а то, что
        # мы пропустили. Смешать одно с другим — испортить любой анализ
        # динамики и временной сплит обучения (первый проход после слепоты
        # 14–26.07.2026 даст ~20–25 тыс лотов с почти одинаковым first_seen).
        #
        # Снятия: их дата — момент, когда мы ЗАМЕТИЛИ пропажу, а не когда лот
        # ушёл с рынка. После перерыва «дни на рынке» у этой когорты завышены
        # на всю длину перерыва.
        #
        # source <> 'user' обязателен: пользовательский предикт тоже ставит
        # last_seen = now (UPSERT_SQL_USER). Один человек, открывший карточку
        # посреди слепоты, поднял бы MAX(last_seen) к сегодняшнему дню и
        # замаскировал разрыв целиком — детектор молча перестал бы работать
        # ровно в том сценарии, ради которого заведён.
        gap_row = conn.execute(
            "SELECT MAX(last_seen), julianday('now') - julianday(MAX(last_seen)) "
            "FROM listings WHERE is_active = 1 "
            "AND COALESCE(source, 'scrape') <> 'user'"
        ).fetchone()
        last_observed_at, gap_days = gap_row[0], gap_row[1]
        # Момент старта берём из ТОЙ ЖЕ базы, а не из времени питона: пометка
        # когорты ниже сравнивает с ним first_seen, который проставляет сам
        # SQLite через datetime('now'). Смешивать два источника времени в
        # одном сравнении — напрашиваться на расхождение в доли секунды.
        pass_started_at = conn.execute("SELECT datetime('now')").fetchone()[0]
        observation_gap_days = round(gap_days, 2) if gap_days is not None else None
        recovery_pass = (
            observation_gap_days is not None and observation_gap_days > RECOVERY_GAP_DAYS
        )
        if recovery_pass:
            logger.warning(
                "Восстановительный проход: последний раз наблюдали лоты живыми %s дн. назад "
                "(порог %s). Найденные «новые» — это в основном пропущенное за перерыв, "
                "а снятия датируются сегодняшним днём, хотя лоты ушли внутри перерыва",
                observation_gap_days, RECOVERY_GAP_DAYS,
            )
    found: dict[int, int | None] = {}
    # issue #166: найденные id ПО ШАРДАМ (а не только общим котлом): состав
    # шарда — это фактический сток выдачи для квот докачки и источник
    # атрибуции id → шард.
    shard_found: dict[str, dict[int, int | None]] = {}
    failed_shards: list[str] = []
    banned = False
    banned_phase: str | None = None
    pass_t0 = _now()
    deadline = pass_t0 + time_budget_min * 60.0
    time_budget_hit = False
    search_pages = 0  # issue #152: фактические запросы/секунды фаз — в план
    search_seconds = 0.0  # и в тайминги sweep_runs для следующих проходов
    detail_requests = 0
    detail_seconds = 0.0

    def out_of_time() -> bool:
        return _now() >= deadline

    with PoliteClient(delay_range=delay_range) as client:
        shards = shard_urls(deal)
        # issue #166: порядок обхода ротируется между запусками. Смещение
        # считается от монотонного номера прохода (счётчик в sweep_state)
        # шагом, взаимно простым с числом шардов (shard_plan.rotation_offset):
        # шаг +1 при покрытии k из 32 давал шарду 32−k подряд непокрытых
        # проходов — та же спутанность дня с составом с длинным периодом
        # (ревью). Иначе обрыв прохода (бюджет, бан) ампутирует один и тот же
        # хвост алфавита, и «день» снова становится «районом».
        #
        # issue #168: номер ИНКРЕМЕНТИРУЕТСЯ здесь, до фазы выдачи, отдельной
        # транзакцией — а не в конце прохода в транзакции итогов. Мягкий
        # дедлайн (--time-budget-min) проход завершал, и счётчик ехал; но
        # timeout-minutes раннера рубит job вместе с транзакцией итогов —
        # номер оставался прежним, следующий запуск повторял то же смещение
        # и обрезал тот же хвост шардов (залипание ротации). Цена раннего
        # инкремента — пропуск номера при падении на старте; номера не
        # обязаны быть плотными, от них нужна только монотонность.
        with get_conn(db_path) as conn:
            pass_seq = advance_sweep_pass_seq(conn, deal)
        rotation = rotation_offset(pass_seq, len(shards))
        ordered_shards = rotated(shards, rotation)
        if ordered_shards[0] != shards[0]:
            logger.info(
                "Ротация обхода: старт с шарда «%s» (смещение %s/%s)",
                ordered_shards[0][0], rotation, len(shards),
            )
        search_started = _now()
        for i, (label, base_url) in enumerate(ordered_shards):
            if out_of_time():
                # Недообойдённые шарды — в failed_shards: их объявления не
                # получили last_seen, и без этой пометки они уехали бы в
                # delisted (issue #96).
                time_budget_hit = True
                remaining = [lbl for lbl, _ in ordered_shards[i:]]
                failed_shards.extend(remaining)
                logger.error(
                    "Бюджет времени (%s мин) исчерпан на фазе выдачи — не обойдено шардов: %s",
                    time_budget_min, len(remaining),
                )
                break
            shard_ids, covered, ban_exc, shard_pages = _sweep_shard(
                client, label, base_url, max_pages
            )
            search_pages += shard_pages
            if not covered:
                failed_shards.append(label)
            else:
                shard_found[label] = shard_ids
            # Частично обойдённые страницы упавшего/забаненного шарда тоже
            # честно увидены: их id получают sighting/last_seen/цену и
            # атрибуцию — не выбрасываем их только из-за того, что хвост
            # пагинации не лёг (при BanDetected — то же, что при сетевом
            # сбое: до #166 частичные выживали в общем словаре).
            found.update(shard_ids)
            # Атрибуция id → шард пишется для КАЖДОГО найденного id (а не
            # только новых): backlog, набранный до ввода схемы, атрибутируется
            # сам с первого же прохода — ручная инициализация не нужна.
            record_listing_shards([(lid, label) for lid in shard_ids], db_path)
            if ban_exc is not None:
                banned_phase = "search"
                logger.critical(
                    "%s — прерываем проход на шарде «%s», остальные шарды не обходим",
                    ban_exc,
                    label,
                )
                banned = True
                _alert_ban(ban_exc)
                break

        search_seconds = _now() - search_started

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

        # Очередь detail fetch — лоты с сайтингом, но без деталей
        # (title IS NULL — сентинел «есть только sighting, детали ещё нет»).
        # is_active = 1 обязателен: лот может получить sighting и быть снят
        # с продажи (is_active=0) раньше, чем очередь до него дойдёт — без
        # фильтра такой «труп» навсегда остаётся с title IS NULL (детальная
        # страница отдаёт 404 → parse_detail() -> None → title не
        # заполняется) и застревает в голове очереди, съедая бюджет докачки
        # на каждом проходе.
        #
        # issue #166: дневная порция — СМЕСЬ ГОРОДА, а не его срез.
        # «Круговая» очередь #152 по колонкам listings.district/rooms на
        # сайтингах вырождалась: эти колонки заполняет только детальная
        # страница, поэтому у всего backlog'а они NULL — партиция одна, и
        # порядок фактически `ORDER BY id DESC`: свежесть публикации вместо
        # географии. Свежих лотов больше там, где выше оборачиваемость
        # (Бостандыкский ~24% порции при ~13% стока), а не там, где больше
        # сток (Алатауский ~11% при ~20%+) — TVD дня 0.32–0.35,
        # time_confounding в model_meta застрял на confounded: true.
        #
        # Теперь: дневной лимит раскладывается квотами по шардам
        # «район × комнаты» ПРОПОРЦИОНАЛЬНО ФАКТИЧЕСКОМУ СТОКУ ВЫДАЧИ этого
        # прохода (shard_plan.largest_remainder_quotas). Недобор по шарду
        # (бан, пустая страница, таймаут, мелкий backlog) НЕ отдаётся
        # соседям в этом же проходе — иначе один сбойный район снова
        # перекосит день; он компенсируется следующими проходами через
        # круговой курсор шарда (shard_cursors): отметка не двигается по
        # недокачанным лотам, и окно следующего прохода продолжит с неё.
        # Курсор переживает перезапуск: живёт в базе, едет в релизе.
        with get_conn(db_path) as conn:
            cursors = shard_cursors(db_path, conn=conn)
            fallback_stock = last_known_shard_stock(conn)
            queue_size_before = shard_backlog_count(conn)
            unattributed = unattributed_backlog_count(conn)
            backlog_map = {label: shard_backlog_count(conn, label) for label, _ in shards}
        if unattributed:
            logger.warning(
                "В backlog %s лотов без атрибуции к шарду — не видны в выдаче с "
                "ввода схемы #166. В очередь не берутся: ждут переобнаружения "
                "в выдаче (получат шард) или delisted",
                unattributed,
            )

        # issue #152: потолок докачки — под фактический остаток бюджета и
        # потолок вежливости, ДО входа в фазу докачки. Цена выдачи уже
        # известна (запросы и секунды), цена детали — из истории проходов
        # (навершие над паузой; сама пауза — фактическая, этого прохода).
        # Желаемое ограничено реальной очередью после сайтингов этого
        # прохода: потолок при мелкой очереди не расходует бюджет. Проход,
        # который по плану не влезает, урезает потолок сам, а не упирается
        # в дедлайн на середине очереди.
        want_new_q = min(want_new, sum(backlog_map.values()))
        requests_so_far = sum(getattr(client, "counters", {}).values()) or search_pages
        fit = fit_detail_caps(
            want_new_q,
            want_refresh_q,
            requests_so_far=requests_so_far,
            elapsed_s=_now() - pass_t0,
            budget_s=time_budget_min * 60.0,
            t_detail=sum(delay_range) / 2 + timings.overhead_detail,
        )
        eff_max_new, eff_max_refresh = fit.max_new, fit.max_refresh
        if fit.trimmed:
            logger.warning(
                "Потолок докачки урезан заранее: хотели %s+%s, влезает %s "
                "(причина: %s; выдача уже израсходовала %s запросов за %.0f мин) — "
                "новых %s, refresh %s",
                want_new_q, want_refresh_q, fit.budget_requests, fit.reason,
                requests_so_far, search_seconds / 60, eff_max_new, eff_max_refresh,
            )
        # Замеренный ЭТИМ проходом сток — отдельно от сведённого (с фолбэком):
        # второй проход планировщика (#152) имеет дело только с замеренными
        # шардами — у незамеренных нет строки стока для сверки batch-TVD.
        measured_stock = {
            label: (len(shard_found[label]) if label in shard_found else None)
            for label, _ in shards
        }
        stock = {
            label: (
                measured_stock[label]
                if measured_stock[label] is not None
                # Шард не покрыт этим проходом (бан/сеть/таймаут) — квота по
                # последнему известному стоку: его backlog всё равно надо
                # разгребать. Без единого успешного замера — 0.
                else fallback_stock.get(label, 0)
            )
            for label, _ in shards
        }
        quotas_base = largest_remainder_quotas(stock, eff_max_new)
        # issue #152: второй проход планировщика — остаток квоты шардов с
        # мелким backlog'ом (их окно недоберёт квоту) раздаётся пропорционально
        # стоку замеренным шардам, чей backlog глубже квоты. Незамеренным —
        # ничего (см. redistribute_leftover): инвариант «сбойный шард не
        # отдаёт свою квоту соседям» из #166 сохраняется.
        quota_extra = redistribute_leftover(quotas_base, backlog_map, measured_stock)
        quotas = {s: quotas_base[s] + quota_extra.get(s, 0) for s in quotas_base}
        redistributed_total = sum(quota_extra.values())
        if redistributed_total:
            logger.info(
                "Остаток квоты от шардов с мелким backlog'ом перераспределён: "
                "%s лотов (%s)",
                redistributed_total,
                ", ".join(f"{s} +{n}" for s, n in quota_extra.items() if n),
            )
        # issue #168: осознанный ОТКАЗ от «пола» (минимальной квоты шарду с
        # непустым backlog'ом и нулевой долей). largest_remainder_quotas
        # трогать нельзя (проверена перебором в #167), а любой пол снаружи
        # неё либо добавляет запросы сверх дневного потолка — ломая инвариант
        # «число запросов за проход не изменилось», — либо отбирает квоту у
        # замеренных шардов: перекос порции, невидимый для batch-TVD (у
        # незамеренного шарда нет строки стока, с которой сверяется TVD, —
        # его докачка ухудшала бы состав незаметно). Реальное лечение
        # замороженного шарда — покрытие (разгон глубины выдачи живёт в
        # #152), а не арифметика квот; молчание при этом недопустимо —
        # поэтому ниже событие starved_shards.
        plan: list[dict] = []
        for label, _ in ordered_shards:
            quota = quotas.get(label, 0)
            with get_conn(db_path) as conn:
                window, wrapped = shard_backlog_window(conn, label, cursors.get(label), quota)
            plan.append({
                "shard": label,
                "stock": measured_stock[label],
                "quota": quota,
                "quota_base": quotas_base.get(label, 0),
                "quota_extra": quota_extra.get(label, 0),
                "window": window,
                "wrapped": wrapped,
                "backlog_before": backlog_map[label],
                "fetched": 0,
                "last_ok": None,
            })
        logger.info(
            "План докачки: %s лотов (потолок %s) по шардам: %s",
            sum(p["quota"] for p in plan),
            eff_max_new,
            ", ".join(f"{p['shard']}={p['quota']}" for p in plan if p["quota"]),
        )
        # issue #168: шард с непустым backlog'ом и нулевой квотой N проходов
        # подряд — событие, а не тишина. Типовой сценарий — хронически
        # усечённый шард (выдача глубже max_pages → гард #152: stock NULL →
        # нет строки в last_known_shard_stock → квота 0 на каждом проходе):
        # backlog растёт, а TVD порции его не видит (шарда нет ни в факте,
        # ни в стоке), unattributed_backlog_count молчит (атрибуция есть).
        # Серия считается по записанным проходам (sweep_shard_stats) плюс
        # текущий план; флаг уезжает в stats["starved_shards"] → summary-json
        # → вердикт утреннего отчёта админу — путь до алерта уже существует.
        starved_shards: list[str] = []
        if eff_max_new <= 0:
            # issue #152 (хвост ревью #169): при нулевом потолке докачки
            # (--max-new 0, диагностический прогон) quota=0 у ВСЕХ шардов
            # сразу — через STARVED_SHARD_STREAK проходов в красный вердикт
            # уезжали бы все 32. Заморозка — свойство конкретного шарда при
            # работающей очереди; с выключенной очередью её нет вовсе.
            # Диагностические строки sweep_shard_stats (pass_cap=0) серии
            # не портят: zero_quota_streak их пропускает.
            logger.info(
                "Потолок докачки 0 (диагностический проход) — квоты не "
                "раздаются, заморозка шардов не считается"
            )
        else:
            with get_conn(db_path) as conn:
                for p in plan:
                    if p["quota"] or not p["backlog_before"]:
                        continue
                    streak = 1 + zero_quota_streak(
                        conn, p["shard"], pass_seq, STARVED_SHARD_STREAK - 1
                    )
                    if streak >= STARVED_SHARD_STREAK:
                        starved_shards.append(p["shard"])
        if starved_shards:
            logger.error(
                "Шарды с непустым backlog'ом и нулевой квотой %s прохода подряд: "
                "%s — порция их молча обходит, backlog растёт незаметно для TVD. "
                "Квотой это не лечится (см. комментарий выше) — нужно покрытие "
                "этих шардов выдачей (разгон глубины живёт в #152)",
                STARVED_SHARD_STREAK,
                ", ".join(starved_shards),
            )
        new_count = 0
        stop_details = banned  # бан на выдаче — детали не качаем вовсе
        details_started = _now()
        for p in plan:
            if stop_details:
                break
            for lid in p["window"]:
                if out_of_time():
                    # Проверяем перед КАЖДЫМ запросом, а не раз в шард: один
                    # шард это до 250 страниц ≈ 13 минут, промах на такую
                    # величину съедает весь запас на заливку базы.
                    time_budget_hit = True
                    logger.error("Бюджет времени исчерпан на докачке деталей — мягкий стоп")
                    stop_details = True
                    break
                try:
                    detail_html = client.get(f"https://krisha.kz/a/show/{lid}")
                    detail_requests += 1
                except BanDetected as exc:
                    logger.critical("%s — прерываем докачку деталей", exc)
                    if not banned:
                        banned_phase = "details"
                        _alert_ban(exc)
                    banned = True
                    stop_details = True
                    break
                listing = (
                    parse_detail(detail_html, f"https://krisha.kz/a/show/{lid}")
                    if detail_html
                    else None
                )
                if listing is not None:
                    upsert_listing(listing, db_path, price_bounds=bounds)
                    new_count += 1
                    p["fetched"] += 1
                    # Курсор двигаем только по УСПЕШНО докачанным: недобор
                    # (404, таймаут, битый парс) остаётся ниже отметки и
                    # компенсируется следующими проходами, а не чужой квотой.
                    p["last_ok"] = lid
            if p["quota"] or p["fetched"]:
                logger.info(
                    "Шард «%s»: план %s, факт %s (backlog %s→%s%s)",
                    p["shard"],
                    p["quota"],
                    p["fetched"],
                    p["backlog_before"],
                    p["backlog_before"] - p["fetched"],
                    ", курсор с головы" if p["wrapped"] else "",
                )
        queue_size_after = queue_size_before - new_count
        cursor_updates = {
            p["shard"]: p["last_ok"] for p in plan if p["last_ok"] is not None
        }
        shard_stat_rows = [
            {
                "run_seq": pass_seq,
                "started_at": pass_started_at,
                "shard": p["shard"],
                "stock": p["stock"],
                "quota": p["quota"],
                "fetched": p["fetched"],
                "backlog_before": p["backlog_before"],
                "backlog_after": p["backlog_before"] - p["fetched"],
                "cursor_after": cursor_updates.get(p["shard"], cursors.get(p["shard"])),
                "wrapped": p["wrapped"],
                # issue #152: действующий потолок прохода — диагностические
                # прогоны (pass_cap=0) не портят серии zero_quota_streak.
                "pass_cap": eff_max_new,
            }
            for p in plan
        ]

        # issue #102: отдельная лимитированная очередь — активные лоты,
        # уже имеющие детали (title IS NOT NULL), но не докачанные дольше
        # refresh_stale_days. Без этого отредактированные продавцом
        # площадь/этаж/описание/координаты никогда не подтягиваются, пока
        # объявление живёт — рескрейп по карточке обновляет только цену.
        # Самые давно не докачанные — первыми (ORDER BY scraped_at ASC).
        refresh_queue_size = 0
        refreshed_count = 0
        if not banned and eff_max_refresh > 0:
            with get_conn(db_path) as conn:
                refresh_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM listings WHERE title IS NOT NULL AND is_active = 1 "
                        "AND julianday('now') - julianday(scraped_at) > ? "
                        "ORDER BY scraped_at ASC LIMIT ?",
                        (eff_stale_days, eff_max_refresh),
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
                    detail_requests += 1
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
        detail_seconds = _now() - details_started

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
        delist_share: float | None = None
        if not failed_shards:
            where = (
                "is_active = 1 "
                f"AND julianday('now') - julianday(last_seen) > {DELIST_AFTER_DAYS}"
            )
            candidates = conn.execute(f"SELECT COUNT(*) FROM listings WHERE {where}").fetchone()[0]
            # issue #152: гард на массовое снятие. Нормальный delist — 1–4% в
            # день (замерено по delisted_at прода: медиана ~2.2%); доля в разы
            # выше означает, что выдача частично отдала пустые 200 без анти-бот
            # маркеров (шард формально «покрыт»). Пометить их снятыми — испортить
            # ликвидность и «дни на рынке» разом по всей базе. Смотрим именно на
            # ДОЛЮ: после разгона активных станет ~40k, и абсолютные пороги соврут.
            #
            # Знаменатель — именно active_in_db, снятый ДО прохода, а НЕ число
            # активных на момент решения. Кандидатом на снятие может стать
            # только лот, который входил в проход активным; всё, что этот
            # проход увидел впервые, только что получило last_seen = now и в
            # числитель попасть не может. Считать долю от популяции, часть
            # которой физически не может оказаться в числителе, — разбавлять
            # знаменатель и глушить гард ровно в тот момент, когда приток велик.
            #
            # Побочное следствие, которое выглядит поломкой, но ею не является:
            # первый проход после 13-дневной слепоты даёт кандидатов 25–44% от
            # 15.4k активных (1-0.978^13 .. 1-0.956^13 по замеренной суточной
            # доле 1.1–4.4%), то есть при высоких значениях гард сработает и
            # снятие не проставится. Это ШТАТНО и само рассосётся за сутки:
            # на следующем проходе active_in_db уже включает бэкфилл (~38k),
            # доля падает до 15–18% и снятие проходит. Пороги не поднимать и
            # руками не чинить — delisted_at когорты уедет на день, а честный
            # интервал цензурирования [last_seen, delisted_at] это отразит.
            share = candidates / active_in_db if active_in_db else 0.0
            delist_share = round(share, 4)
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

        # issue #156: запись о провале ставится в КОНЦЕ прохода, хотя сам
        # разрыв измерен в начале. Причина — поле note: только здесь известно,
        # обошли ли мы выдачу целиком. От этого зависит, надо ли метить
        # когортой следующие проходы (см. ниже).
        #
        # INSERT OR IGNORE: если джобу перезапустят, тот же интервал повторно
        # не ляжет — ключ (gap_start, gap_end) совпадёт только при точном
        # повторе, а новый запуск даст новый gap_end и честно зафиксирует,
        # что провал оказался длиннее.
        incomplete = bool(failed_shards) or time_budget_hit
        if recovery_pass:
            conn.execute(
                "INSERT OR IGNORE INTO data_gaps (gap_start, gap_end, note) "
                "VALUES (?, ?, ?)",
                (
                    last_observed_at,
                    pass_started_at,
                    "incomplete" if incomplete else "complete",
                ),
            )

        # Помечает когорту тот же проход, который её создаёт. Откладывать на
        # пост-обработку релизной базы нельзя: Space рестартует сразу после
        # заливки, и непомеченная когорта успеет утечь в прод — в «срок
        # продажи» и в статистику притока.
        #
        # Предикат по first_seen ловит ровно строки, созданные этим проходом:
        # first_seen проставляется только на INSERT и дальше иммутабелен
        # (его нет в ON CONFLICT SET ни у одного из UPSERT'ов).
        #
        # Grace-окно нужно НЕ всегда. Сайтинг пишется для каждого найденного
        # id без всякого лимита (лимит стоит только на докачке деталей),
        # поэтому при полном обходе вся волна бэкфилла получает first_seen за
        # ОДИН проход, и метить следующие дни незачем — там уже честная
        # органика, ~850 лотов в день, которую мы бы молча выбросили из
        # ликвидности и обучения. Растягивать пометку нужно ровно тогда,
        # когда предыдущий проход не добрал выдачу: часть волны придёт
        # позже. Поэтому смотрим на note предыдущего прохода, а не на
        # календарь.
        cohort_marked = 0
        gap_row = conn.execute(
            "SELECT gap_start FROM data_gaps "
            "WHERE julianday('now') - julianday(gap_end) <= ? AND note = 'incomplete' "
            "ORDER BY gap_end DESC LIMIT 1",
            (GAP_COHORT_GRACE_DAYS,),
        ).fetchone() if not recovery_pass else (last_observed_at,)
        if gap_row is not None:
            cohort = f"gap:{str(gap_row[0])[:10]}"
            cohort_marked = conn.execute(
                "UPDATE listings SET first_seen_cohort = ? "
                "WHERE first_seen >= ? AND first_seen_cohort IS NULL "
                "AND COALESCE(source, 'scrape') <> 'user'",
                (cohort, pass_started_at),
            ).rowcount
            if cohort_marked:
                logger.warning(
                    "Помечено когортой %s: %s лотов. Их first_seen — дата первого "
                    "наблюдения ПОСЛЕ провала, а не публикации: лот мог висеть "
                    "на рынке сколько угодно до этого",
                    cohort, cohort_marked,
                )

        # issue #166: сдвиг круговых курсоров и план/факт по шардам — в той же
        # транзакции, что и итоги прохода: курсор это состояние очереди, и его
        # расходиться с базой (которая уезжает в релиз) нельзя.
        for shard, cursor_id in cursor_updates.items():
            advance_shard_cursor(shard, cursor_id, db_path, conn=conn)
        record_sweep_shard_stats(shard_stat_rows, db_path, conn=conn)
        # Номер прохода (источник ротации и run_seq в stats) здесь НЕ
        # двигается: с issue #168 он инкрементируется в начале прохода,
        # отдельной транзакцией — иначе жёстко убитый раннер залипал бы на
        # том же смещении ротации (см. advance_sweep_pass_seq).

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

    # issue #166: контроль состава порции той же метрикой, что в model_meta —
    # вторую реализацию TVD не пишем, берём validity.total_variation_distance.
    # Сравниваем фактически докачанное с ФАКТИЧЕСКИМ стоком выдачи этого
    # прохода (не с составом базы — база сама ещё несёт перекос прошлого).
    batch_tvd_district: float | None = None
    batch_tvd_shard: float | None = None
    fetched_labels = [p["shard"] for p in plan for _ in range(p["fetched"])]
    stock_labels = [label for label, n in stock.items() for _ in range(max(0, n))]
    if fetched_labels and stock_labels:
        batch_tvd_shard = round(total_variation_distance(fetched_labels, stock_labels), 3)
        batch_tvd_district = round(
            total_variation_distance(
                [shard_district(s) for s in fetched_labels],
                [shard_district(s) for s in stock_labels],
            ),
            3,
        )
        logger.info(
            "Состав порции против стока выдачи: TVD по районам %.3f, по шардам %.3f "
            "(порог %.2f)",
            batch_tvd_district,
            batch_tvd_shard,
            MAX_TEST_TVD,
        )

    # Серия банов — фиксируется в конце прохода (issue #152): два подряд
    # BanDetected → следующие проходы идут со steady-паузами, пока чистый
    # проход не сбросит серию. Алерт — на фронте серии (переходе через
    # порог), не каждый проход: повтор под активным откатом был бы спамом.
    ban_streak_end = 0 if not banned else ban_streak_at_start + 1
    with get_conn(db_path) as conn:
        record_consecutive_bans(conn, deal, ban_streak_end)
    if (
        ban_streak_end >= BAN_ROLLBACK_STREAK
        and ban_streak_at_start < BAN_ROLLBACK_STREAK
    ):
        logger.error(
            "Откат по бану: %s подряд прохода с BanDetected — следующие "
            "проходы пойдут со steady-паузами до первого чистого",
            ban_streak_end,
        )
        _alert_ban_rollback(ban_streak_end)

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
        # Доля кандидатов на снятие от активных на момент решения. Нужна в
        # отчёте, даже когда гард промолчал: 2% и 25% — это разные ночи, а по
        # одному только delisted их не отличить от «шард упал, снятие пропущено».
        "delist_share": delist_share,
        # issue #156: маркер когорты. Проход после перерыва даёт «новых» и
        # «снятых», которые не являются событиями рынка — по этим двум ключам
        # когорту можно отделить задним числом, не помня дат наизусть.
        "observation_gap_days": observation_gap_days,
        "recovery_pass": recovery_pass,
        "cohort_marked": cohort_marked,
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
        # issue #154: потолок докачки нужен в отчёте, чтобы «докачано 1000»
        # можно было отличить от «докачано 1000 из 1000, то есть упёрлись».
        # Ровно эта неразличимость двенадцать дней подряд прятала отставание.
        # С #152 это ЭФФЕКТИВНЫЙ потолок прохода (пресет режима/оверрайд,
        # урезанный fit_detail_caps под бюджет и вежливость) — сравнение
        # «докачано == потолок» в утреннем отчёте честно только с ним.
        "max_new_details": eff_max_new,
        # issue #166: план квот по шардам и TVD фактически докачанной порции
        # против стока выдачи. Подробный план/факт по каждому шарду — в
        # таблице sweep_shard_stats.
        "detail_plan": {p["shard"]: p["quota"] for p in plan if p["quota"]},
        "batch_tvd_district": batch_tvd_district,
        "batch_tvd_shard": batch_tvd_shard,
        # issue #168: шарды, замороженные для очереди докачки — непустой
        # backlog при нулевой квоте STARVED_SHARD_STREAK проходов подряд.
        # Пустой список — штатное состояние. Непустой уезжает в summary-json
        # и дальше в вердикт утреннего отчёта админу (daily_report).
        "starved_shards": starved_shards,
        # issue #152: режим прохода и его обоснование — решение принято в
        # начале прохода по состоянию базы и должно быть видно в итогах.
        "mode": preset.name,
        "mode_reason": mode_reason,
        "drain_completed": drain_completed,
        "backlog_at_start": backlog_at_start,
        "delay_range": [delay_range[0], delay_range[1]],
        # Откат по серии банов: паузы возвращены к steady (2 прохода подряд
        # с BanDetected, см. pass_plan.BAN_ROLLBACK_STREAK).
        "ban_rollback": ban_rollback,
        "ban_streak": ban_streak_end,
        # Оценка прохода до его начала (тайминги — фактические, из истории
        # sweep_runs после мержа #152, до того — откалиброванные фолбэки).
        "plan_estimate": {
            "est_requests": plan_estimate.est_requests,
            "est_seconds": round(plan_estimate.est_seconds, 1),
            "budget_seconds": plan_estimate.budget_seconds,
            "fits": plan_estimate.fits,
            "mean_rps": round(plan_estimate.mean_rps, 3),
            "timing_samples": timings.samples,
        },
        # Потолок, урезанный заранее (до фазы докачки) под фактический
        # остаток бюджета/потолок вежливости; None — влезали без подрезки.
        "plan_trimmed": (
            {
                "wanted_new": want_new_q,
                "wanted_refresh": want_refresh_q,
                "budget_requests": fit.budget_requests,
                "reason": fit.reason,
            }
            if fit.trimmed
            else None
        ),
        "max_refresh": eff_max_refresh,
        "refresh_stale_days": eff_stale_days,
        # Остаток квоты, перераспределённый вторым проходом планировщика
        # от шардов с мелким backlog'ом замеренным шардам с глубоким.
        "quota_redistributed": redistributed_total,
        # Лоты backlog'а без атрибуции к шарду — второй способ тихой
        # заморозки (первый — starved_shards); в утренний отчёт едут оба.
        "unattributed_backlog": unattributed,
        # Тайминги фаз — в sweep_runs, чтобы план следующих проходов
        # считался по фактическим замерам, а не по константам.
        "search_pages": search_pages,
        "search_seconds": round(search_seconds, 1),
        "detail_requests": detail_requests,
        "detail_seconds": round(detail_seconds, 1),
    }

    # issue #154: история проходов едет в базе, а не в файле — раннер каждый
    # раз чистый, а база скачивается из релиза и заливается обратно. Без неё
    # инварианты «очередь растёт третий проход подряд» и «упёрлись в потолок
    # N дней подряд» невычислимы в принципе.
    record_sweep_run(
        {
            "started_at": pass_started_at,
            "deal": deal,
            "failed_shards": len(failed_shards),
            "search_pages": search_pages,
            "search_seconds": round(search_seconds, 1),
            "detail_requests": detail_requests,
            "detail_seconds": round(detail_seconds, 1),
            "delay_lo": delay_range[0],
            "delay_hi": delay_range[1],
            "mode": preset.name,
            **{k: stats.get(k) for k in (
                "found_in_search", "discovered_new", "details_fetched",
                "max_new_details", "detail_queue_after", "price_changes",
                "delisted", "recovery_pass", "suspicious",
            )},
        },
        db_path,
    )
    logger.info(
        "Рескрейп: в выдаче %(found_in_search)s, знакомых %(known_seen)s, "
        "новых %(discovered_new)s (докачано деталей %(details_fetched)s), "
        "изменений цены %(price_changes)s, снято %(delisted)s, "
        "очередь деталей %(detail_queue_after)s, обновлено устаревших %(stale_refreshed)s",
        {**stats, "delisted": stats["delisted"] if stats["delisted"] is not None else "n/a"},
    )
    return stats
