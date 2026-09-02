"""SQLite-хранилище объявлений. Одна таблица `listings`, upsert по id."""

import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from krisha.config import (
    ALMATY_BBOX,
    AREA_MAX,
    AREA_MIN,
    DB_PATH,
    PRICE_MAX,
    PRICE_MIN,
    RENT_PRICE_MAX,
    RENT_PRICE_MIN,
    SHARED_PIN_MIN,
    listing_shard_label,
)

logger = logging.getLogger(__name__)

# Скачок цены > этой доли за одну точку истории — подозрительно (issue #98:
# структурный парсинг убрал основной источник, но sanity-фильтр как второй
# рубеж защиты от съехавших/битых данных). Пишем в history и логируем
# warning, но НЕ блокируем запись — иначе легитимный резкий дисконт навсегда
# застрянет и не обновится.
PRICE_JUMP_ALERT_RATIO = 0.6

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id              INTEGER PRIMARY KEY,          -- id объявления на Krisha
    url             TEXT NOT NULL,
    title           TEXT,
    price           INTEGER,                      -- ₸
    rooms           INTEGER,
    area            REAL,                         -- м²
    floor           INTEGER,
    total_floors    INTEGER,
    building_type   TEXT,                         -- монолитный / кирпичный / панельный ...
    year_built      INTEGER,
    ceiling         REAL,                         -- м
    district        TEXT,
    microdistrict   TEXT,
    street          TEXT,
    house_num       TEXT,
    address_title   TEXT,
    complex_name    TEXT,
    lat             REAL,
    lon             REAL,
    user_type       TEXT,                         -- owner / agent / company / complex
    category        TEXT,                         -- vtorichka / novostroiki
    description     TEXT,
    photos_count    INTEGER,
    raw_params      TEXT,                         -- JSON всех распарсенных параметров
    scraped_at      TEXT DEFAULT (datetime('now')),
    first_seen      TEXT,                         -- этап 4: когда впервые увидели
    last_seen       TEXT,                         -- этап 4: когда видели в выдаче в последний раз
    is_active       INTEGER DEFAULT 1,            -- этап 4: 0 = пропало из выдачи (продано/снято)
    delisted_at     TEXT                          -- этап 4: когда пометили снятым
);
CREATE INDEX IF NOT EXISTS idx_listings_district ON listings(district);
CREATE INDEX IF NOT EXISTS idx_listings_rooms ON listings(rooms);

-- Дедуп апдейтов Telegram МЕЖДУ процессами: при нескольких воркерах uvicorn
-- (WEB_CONCURRENCY) ретрай одного и того же update_id может прилететь в
-- другой процесс, где памяти о нём нет, — и бот ответит дважды.
CREATE TABLE IF NOT EXISTS tg_updates (
    update_id  INTEGER PRIMARY KEY,
    seen_at    TEXT DEFAULT (datetime('now'))
);

-- Этап 4: история цены объявления. Строка добавляется при первом появлении
-- и при каждом изменении цены, замеченном рескрейпом.
CREATE TABLE IF NOT EXISTS price_history (
    listing_id  INTEGER NOT NULL,
    price       INTEGER NOT NULL,
    observed_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    PRIMARY KEY (listing_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id);

-- issue #128: лог каждого предикта (пользовательского и канального/алертов).
-- Без этого нельзя проверить продуктовую метрику — совпадает ли вердикт
-- с последующей судьбой лота (снижение цены / срок на рынке).
CREATE TABLE IF NOT EXISTS predictions (
    listing_id    INTEGER,
    predicted_at  TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
    fair_price    REAL,
    fair_low      REAL,
    fair_high     REAL,
    verdict       TEXT,
    model_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_predictions_listing ON predictions(listing_id);

CREATE TABLE IF NOT EXISTS complexes (
    id                  INTEGER PRIMARY KEY,      -- id ЖК на Krisha
    url                 TEXT,
    name                TEXT,                     -- «Maxima City» (без префикса ЖК)
    name_norm           TEXT,                     -- нормализованное имя для джойна
    region              TEXT,
    address             TEXT,
    developer           TEXT,                     -- застройщик
    housing_class       TEXT,                     -- эконом / комфорт / бизнес / премиум
    completion_year     INTEGER,                  -- год сдачи (последняя очередь)
    deadline_text       TEXT,
    construction_status TEXT,                     -- «Сдан в эксплуатацию» / «Строится»
    material            TEXT,                     -- монолитный / кирпичный / панельный
    max_floors          INTEGER,
    facing              TEXT,                     -- отделка
    apartments_count    INTEGER,
    lat                 REAL,
    lon                 REAL,
    raw_params          TEXT,                     -- JSON всех распарсенных параметров
    scraped_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_complexes_name_norm ON complexes(name_norm);

-- issue #103: данные, отклонённые data-contract проверкой в upsert_listing
-- (цена/площадь/координаты вне разумного диапазона) — не молча в listings,
-- а сюда: аудит + метрика качества парсинга (COUNT(*) по detected_at).
CREATE TABLE IF NOT EXISTS parse_anomalies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   INTEGER NOT NULL,
    field        TEXT NOT NULL,
    reason       TEXT NOT NULL,
    raw_value    TEXT,
    detected_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_parse_anomalies_listing ON parse_anomalies(listing_id);

-- issue #156: интервалы, когда сбор не работал. Строку пишет САМ первый
-- успешный проход после провала — sweep() сравнивает now с MAX(last_seen)
-- только что скачанной базы. Ни одной вписанной руками даты: версия кода и
-- версия базы распространяются независимо, поэтому константа в коде
-- протухла бы к следующему инциденту, а у аренды провал вообще свой
-- (13.07 17:17 UTC против 04:33 у продажи — разные базы, разные границы).
--
-- Источник истины именно база, а не файл в репозитории: база — единственный
-- артефакт, который едет вместе с данными (релиз db-latest → раннер → Space).
-- issue #154: счётчики каждого прохода. Без них инварианты «очередь деталей
-- растёт третий проход подряд» и «докачано ровно max_new N дней подряд»
-- невычислимы: раннер GitHub Actions каждый раз чистый, а файл истории
-- лежит в .gitignore и до прода не доезжает. База — единственное состояние,
-- которое переживает проход (скачивается из релиза и заливается обратно).
--
-- Ровно та слепота, из-за которой отставание сбора было невидимым: докачка
-- двенадцать дней подряд упиралась в потолок 1000/день, а в отчёте это
-- выглядело как «новых 1000» — то есть как успех.
--
-- Одна строка в сутки: 365 строк в год, на размер базы не влияет.
CREATE TABLE IF NOT EXISTS sweep_runs (
    -- issue #171: время с МИЛЛИСЕКУНДАМИ (strftime('%Y-%m-%d %H:%M:%f')).
    -- Секундной точности не хватало: ключ + INSERT OR REPLACE схлопывал два
    -- прохода, стартовавших в одну секунду, в одну строку — история теряла
    -- запись молча. Старые строки (без долей секунды) сортируются с новыми
    -- корректно: "…:30" < "…:30.123" и лексикографически, и по времени.
    started_at         TEXT PRIMARY KEY,
    deal               TEXT,
    found_in_search    INTEGER,
    discovered_new     INTEGER,
    details_fetched    INTEGER,
    -- issue #152 (ревью #170): потолок разделён на заявленный (пресет
    -- режима или явный оверрайд — то, чем сбор ограничен ЗАМЫСЛОМ) и
    -- эффективный (после подрезки реальной очередью и бюджетом). Детектор
    -- «упёрлись в лимит» сравнивает докачку с эффективным И требует
    -- непустой очереди после прохода; иначе здоровое «очередь разобрана
    -- в ноль» рапортовалось бы как «упёрлись» (очередь < потолка сжимает
    -- эффективный до очереди, и равенство выполнялось бы тождественно).
    max_new_details    INTEGER,   -- заявленный потолок режима/оверрайда
    max_new_effective  INTEGER,   -- потолок после подрезки очередью/бюджетом
    detail_queue_after INTEGER,
    price_changes      INTEGER,
    delisted           INTEGER,   -- NULL = детект пропущен
    failed_shards      INTEGER,
    recovery_pass      INTEGER,
    suspicious         INTEGER,
    -- issue #152: фактические тайминги фаз и режим прохода. Без них «план
    -- прохода по фактическим таймингам» невозможен — до мержа в таблице был
    -- только started_at. Старые строки держат NULL и игнорируются оценщиком.
    search_pages       INTEGER,   -- страниц выдачи обойдено (запросов фазы)
    search_seconds     REAL,      -- стенные часы фазы выдачи
    detail_requests    INTEGER,   -- запросов докачки (new + refresh + неудачи)
    detail_seconds     REAL,      -- стенные часы фазы докачки
    delay_lo           REAL,      -- фактические паузы клиента этого прохода
    delay_hi           REAL,      -- (нужны, чтобы отделить паузу от latency)
    mode               TEXT       -- drain|steady — режим по backlog'у (#152)
);

CREATE TABLE IF NOT EXISTS data_gaps (
    gap_start   TEXT NOT NULL,   -- MAX(last_seen) до провала: последнее наблюдение
    gap_end     TEXT NOT NULL,   -- старт первого прохода, который провал заметил
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    note        TEXT,
    PRIMARY KEY (gap_start, gap_end)
);

-- issue #166: атрибуция «id объявления → шард выдачи (район × комнаты)».
-- Сайтинг (record_sighting) пишет только id/url/price — district/rooms у
-- лота без деталей неизвестны, поэтому круговая очередь #152 по колонкам
-- listings вырождалась в одну партицию ('?', -1) и фактически работала как
-- ORDER BY id DESC: свежесть публикации вместо географии. Отсюда перекос
-- дневной порции (Бостандыкский ~24% при доле стока ~13%, Алатауский ~11%
-- при ~20%+; TVD дня 0.32–0.35). Атрибуцию знает фаза выдачи: шард,
-- в котором лот найден, и есть его «район × комнаты» (фильтры шардов
-- непересекаются). Пишется для КАЖДОГО найденного id на каждом проходе —
-- бэклог, набранный до этой схемы, атрибутируется сам с первого же прохода,
-- ручная инициализация не нужна.
CREATE TABLE IF NOT EXISTS listing_shards (
    listing_id  INTEGER PRIMARY KEY,
    shard       TEXT NOT NULL,          -- метка из shard_urls(), «Район Nк»
    seen_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_listing_shards_shard ON listing_shards(shard);

-- issue #166: круговой курсор докачки внутри шарда. last_id — водяная отметка
-- по id (выдача шарда упорядочена свежими вперёд, id монотонен): следующее
-- окно берёт id < last_id, а дойдя до дна, заворачивается на голову. Курсор
-- переживает перезапуск прохода (живёт в базе, едет в релизе) и НЕ двигается
-- по неудачно докачанным лотам — недобор компенсируется следующими проходами,
-- а не отдаётся соседним шардам в этом же.
CREATE TABLE IF NOT EXISTS shard_cursors (
    shard       TEXT PRIMARY KEY,
    last_id     INTEGER,                -- NULL = ходока не было, старт с головы
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- issue #166: план/факт докачки по каждому шарду за проход. Без этого о
-- перекосе узнаём только через две недели из model_meta. 32 строки в сутки —
-- на размер базы не влияет. stock=NULL означает «шард не покрыт этим
-- проходом», тогда квота считалась по последнему известному стоку.
-- PK — (run_seq, shard), а не (started_at, shard): run_seq монотонный счётчик
-- из sweep_state и не зависит от точности часов (issue #171 поднял started_at
-- до миллисекунд, но ключом здесь остаётся именно номер прохода).
CREATE TABLE IF NOT EXISTS sweep_shard_stats (
    run_seq         INTEGER NOT NULL,   -- номер прохода (sweep_state pass_seq)
    shard           TEXT NOT NULL,
    started_at      TEXT NOT NULL,      -- для чтения человеком, не для сортировки
    stock           INTEGER,            -- найдено в выдаче этим проходом
    quota           INTEGER,            -- план докачки (largest remainder от стока)
    fetched         INTEGER,            -- факт докачки
    backlog_before  INTEGER,
    backlog_after   INTEGER,
    cursor_after    INTEGER,            -- водяная отметка после прохода
    wrapped         INTEGER,            -- 1 = окно завернулось на голову выдачи
    -- issue #152: действующий потолок докачки этого прохода. Диагностический
    -- прогон (--max-new 0) пишет строки (свежий замер стока ценен для
    -- last_known_shard_stock), но его quota=0 НЕ означает заморозку шарда:
    -- zero_quota_streak такие строки пропускает по этой колонке.
    pass_cap        INTEGER,
    PRIMARY KEY (run_seq, shard)
);

-- issue #166: мелкое состояние, которому нужна монотонность, а не
-- производность от истории. Первый вариант ротации порядка обхода считался от
-- COUNT(sweep_runs): та таблица не счётчик, а история, и выводить из неё
-- монотонность — лишняя связность (issue #171 чинил там же секундную
-- точность ключа, из-за которой строки схлопывались). Явный счётчик
-- монотонен всегда.
-- Хранится СЫРОЙ номер прохода (без модуля): он же run_seq в
-- sweep_shard_stats, а смещение ротации вычисляется из него шагом, взаимно
-- простым с числом шардов (см. shard_plan.rotation_offset).
-- Здесь же — сентинелы разовых миграций (issue #168, ключи вида
-- «migrate:...»): признак «уже сделано на этой базе» должен ехать вместе с
-- базой через релиз, а не зависеть от окружения запуска.
CREATE TABLE IF NOT EXISTS sweep_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now'))
);
"""

LISTING_COLUMNS = [
    "id", "url", "title", "price", "rooms", "area", "floor", "total_floors",
    "building_type", "year_built", "ceiling", "district", "microdistrict",
    "street", "house_num", "address_title", "complex_name", "lat", "lon",
    "user_type", "category", "description", "photos_count", "raw_params",
]

# Обновляем всегда: свежий парс — источник истины для этих полей.
_UPSERT_ALWAYS = ["price", "title", "raw_params"]
# Остальные поля обновляем только непустым значением: COALESCE не даёт
# неполному парсу затереть хорошие данные NULL-ом, но подтягивает свежие
# description/area/floor и т.д., если продавец отредактировал объявление.
# source при конфликте НЕ трогаем: это происхождение первой записи
# (scrape/user); пользовательский предикт не должен переписывать провенанс.
_UPSERT_COALESCE = [
    c for c in [*LISTING_COLUMNS, "fingerprint", "coords_approx"]
    if c != "id" and c not in _UPSERT_ALWAYS
]

_UPSERT_SET = ", ".join(
    [f"{c} = excluded.{c}" for c in _UPSERT_ALWAYS]
    + [f"{c} = COALESCE(excluded.{c}, {c})" for c in _UPSERT_COALESCE]
)

UPSERT_SQL = f"""
INSERT INTO listings (
    {", ".join(LISTING_COLUMNS)},
    source, fingerprint, coords_approx, first_seen, last_seen, is_active
) VALUES (
    {", ".join(":" + c for c in LISTING_COLUMNS)},
    :source, :fingerprint, :coords_approx, datetime('now'), datetime('now'), 1
)
ON CONFLICT(id) DO UPDATE SET
    {_UPSERT_SET},
    last_seen = datetime('now'),
    is_active = 1,
    delisted_at = NULL,
    scraped_at = datetime('now');
"""

# issue #117: пользовательский предикт (predict.py:predict_from_url,
# source="user") не источник истины по цене/параметрам лота — устаревшая
# или закэшированная страница, открытая пользователем, не должна затирать
# свежие данные рескрейпа. На INSERT (лот встречен впервые) пишем всё как
# обычно; разница — только для ON CONFLICT: price/title/raw_params не
# трогаем вовсе (остаются данные последнего скрейпа). Остальные поля ведут
# себя как в обычном скрейп-upsert'е — COALESCE(excluded, старое): свежий
# парс перезаписывает, а NULL из неполного парса не затирает хорошие данные.
_USER_UPSERT_SET = ", ".join(
    f"{c} = COALESCE(excluded.{c}, listings.{c})"
    for c in [*LISTING_COLUMNS, "fingerprint", "coords_approx"]
    if c not in ("id", "price", "title", "raw_params")
)

UPSERT_SQL_USER = f"""
INSERT INTO listings (
    {", ".join(LISTING_COLUMNS)},
    source, fingerprint, coords_approx, first_seen, last_seen, is_active
) VALUES (
    {", ".join(":" + c for c in LISTING_COLUMNS)},
    :source, :fingerprint, :coords_approx, datetime('now'), datetime('now'), 1
)
ON CONFLICT(id) DO UPDATE SET
    {_USER_UPSERT_SET},
    last_seen = datetime('now'),
    is_active = 1,
    delisted_at = NULL;
"""


COMPLEX_COLUMNS = [
    "id", "url", "name", "name_norm", "region", "address", "developer",
    "housing_class", "completion_year", "deadline_text", "construction_status",
    "material", "max_floors", "facing", "apartments_count", "lat", "lon", "raw_params",
]

COMPLEX_UPSERT_SQL = f"""
INSERT INTO complexes ({", ".join(COMPLEX_COLUMNS)})
VALUES ({", ".join(":" + c for c in COMPLEX_COLUMNS)})
ON CONFLICT(id) DO UPDATE SET
    {", ".join(f"{c} = excluded.{c}" for c in COMPLEX_COLUMNS if c != "id")},
    scraped_at = datetime('now');
"""


@contextmanager
def get_conn(db_path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # FastAPI гоняет sync-хендлеры в тредпуле → возможны конкурентные записи
    # (кэши LLM/vision, upsert из predict). WAL пускает читателей параллельно
    # с писателем, busy_timeout ждёт вместо мгновенного "database is locked".
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        # issue #115: synchronous=NORMAL безопасен вместе с WAL (риск потерять
        # committed-транзакции — только при крэше ОС/питания, не самого процесса)
        # и заметно быстрее дефолтного FULL на каждый fsync при частых upsert'ах.
        conn.execute("PRAGMA synchronous = NORMAL")
        # Чтение через mmap вместо read(): страницы базы попадают в page cache
        # ОС один раз и переиспользуются всеми потоками, без копирования в
        # буфер на каждый запрос. 256 МБ — с запасом на текущие 168 МБ базы.
        conn.execute("PRAGMA mmap_size = 268435456")
        # 8 МБ страниц на соединение (дефолт — 2 МБ): под наплывом одни и те же
        # горячие страницы (индексы, свежие лоты) перестают вымываться.
        conn.execute("PRAGMA cache_size = -8000")
    except sqlite3.OperationalError:
        pass  # read-only ФС и т.п. — работаем как раньше
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def use_conn(
    conn: sqlite3.Connection | None, db_path: Path | str = DB_PATH
) -> Iterator[sqlite3.Connection]:
    """issue #110: переиспользовать уже открытое на запрос соединение вместо
    нового `get_conn()` на каждый внутренний вызов (analogs/market/llm_flags/
    vision в цепочке `predict_from_listing` открывали ~8 отдельных SQLite-
    соединений на один HTTP-запрос). Если `conn` передан — просто отдаём его
    (коммит/закрытие — забота вызывающего, который его открыл); если нет —
    ведём себя как раньше: открываем через `get_conn` и коммитим/закрываем сами.
    """
    if conn is not None:
        yield conn
    else:
        with get_conn(db_path) as new_conn:
            yield new_conn


# Этап 4: новые колонки listings для старых баз (CREATE IF NOT EXISTS их не добавит)
_MIGRATION_COLUMNS = {
    "first_seen": "TEXT",
    "last_seen": "TEXT",
    "is_active": "INTEGER DEFAULT 1",
    "delisted_at": "TEXT",
    # Пользовательские объявления: откуда запись и «отпечаток» квартиры для дублей
    "source": "TEXT DEFAULT 'scrape'",
    "fingerprint": "TEXT",
    # issue #103: 1 = координаты сидят на общей метке ЖК (≥ SHARED_PIN_MIN
    # объявлений на той же точке), не на реальном подъезде/доме — использовать
    # в фичах/дедупе как признак пониженной точности координат.
    "coords_approx": "INTEGER DEFAULT 0",
    # issue #156: происхождение first_seen. NULL — органика, first_seen это
    # честная дата первого появления в выдаче. 'initial' — когорта самого
    # первого сбора (первые двое суток), у неё first_seen = дата старта
    # скрейпа, а не публикации. 'gap:YYYY-MM-DD' — лот впервые увиден сразу
    # после провала сбора с таким gap_start: он мог быть опубликован когда
    # угодно раньше, first_seen тут не факт о рынке, а факт о нас.
    #
    # Это дешёвый построчный фильтр для потребителей (ликвидность, сплит
    # обучения, статистика притока) — без него они не отличат волну бэкфилла
    # от рекордного дня на рынке.
    "first_seen_cohort": "TEXT",
}


# issue #152: колонки sweep_runs для таймингов фаз и режима прохода и
# pass_cap для sweep_shard_stats (см. SCHEMA — комментарии при колонках).
_MIGRATION_SWEEP_RUN_COLUMNS = {
    "search_pages": "INTEGER",
    "search_seconds": "REAL",
    "detail_requests": "INTEGER",
    "detail_seconds": "REAL",
    "delay_lo": "REAL",
    "delay_hi": "REAL",
    "mode": "TEXT",
    # issue #152 (ревью #170): эффективный потолок отдельно от заявленного —
    # детектору «упёрлись в лимит» нужны оба (см. SCHEMA).
    "max_new_effective": "INTEGER",
}
_MIGRATION_SWEEP_SHARD_COLUMNS = {
    "pass_cap": "INTEGER",
}


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    for col, decl in _MIGRATION_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {decl}")
    # CREATE TABLE IF NOT EXISTS не добавляет колонки в уже существующие
    # таблицы — тем же паттерном, что и _MIGRATION_COLUMNS для listings.
    for table, cols in (
        ("sweep_runs", _MIGRATION_SWEEP_RUN_COLUMNS),
        ("sweep_shard_stats", _MIGRATION_SWEEP_SHARD_COLUMNS),
    ):
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # Индекс здесь, а не в SCHEMA: колонка fingerprint появляется миграцией.
    # Без него find_duplicate_id() делает full-scan на каждый предикт.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_listings_fingerprint ON listings(fingerprint)"
    )
    # /api/health на каждый заход на сайт спрашивает MAX(last_seen): без
    # индекса SQLite читает всю таблицу (на проде 168 МБ и ~80 мс на запрос),
    # с индексом берёт последнюю запись сразу. Тем же индексом идёт выборка
    # свежих активных лотов для демо-кнопки. Здесь, а не в SCHEMA: в старых
    # базах колонка last_seen появляется миграцией строкой выше.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_last_seen ON listings(last_seen)")
    # Бэкфилл: для старых записей точка отсчёта — момент скрейпа
    conn.execute("UPDATE listings SET first_seen = scraped_at WHERE first_seen IS NULL")
    conn.execute("UPDATE listings SET last_seen = scraped_at WHERE last_seen IS NULL")
    conn.execute("UPDATE listings SET is_active = 1 WHERE is_active IS NULL")
    # Стартовая точка истории цены для всех объявлений без истории.
    # source <> 'user' обязателен: по правилу issue #117 пользовательский
    # предикт намеренно НЕ пишет точку в price_history (устаревшая страница
    # не должна выглядеть как изменение цены), а бэкфилл гонится на каждом
    # init_db — то есть при каждом старте API и в начале каждого прохода
    # рескрейпа — и возвращал этим лотам ровно ту точку, которую #117 убрал.
    conn.execute(
        """INSERT OR IGNORE INTO price_history (listing_id, price, observed_at)
           SELECT id, price, first_seen FROM listings
           WHERE price IS NOT NULL
             AND COALESCE(source, 'scrape') <> 'user'
             AND id NOT IN (SELECT DISTINCT listing_id FROM price_history)"""
    )
    # issue #156: когорта самого первого сбора. У неё first_seen — дата, когда
    # мы запустили скрейпер, а не когда лот опубликовали (левое цензурирование),
    # поэтому её «срок на рынке» — фикция. Раньше это жило неявной эвристикой
    # прямо в market.py:_DELISTED_SQL; теперь метка материализована в строке и
    # доступна всем потребителям одинаково.
    #
    # Вычисляется из данных, без дат в коде: граница — MIN(first_seen) базы
    # плюс двое суток. Предикат IS NULL обязателен — бэкфилл гоняется на
    # КАЖДОМ init_db (старт API, начало каждого прохода), и без него он
    # переписывал бы уже проставленные 'gap:'-метки на 'initial'.
    conn.execute(
        """UPDATE listings SET first_seen_cohort = 'initial'
           WHERE first_seen_cohort IS NULL AND first_seen IS NOT NULL
             AND julianday(first_seen) <=
                 (SELECT julianday(MIN(first_seen)) + 2 FROM listings)"""
    )
    # issue #168: бэкфилл атрибуции id → шард из деталей (#166, ревью) —
    # РАЗОВАЯ миграция под сентинелом, а не шаг каждого init_db. До этой
    # правки он гонялся на каждом старте (включая API-процесс — init_db
    # зовётся из api/app.py): полный проход по listings с десятками тысяч
    # INSERT OR IGNORE за здоровую загрузку — цена, которую нельзя платить
    # на каждом старте за таблицу, чей результат очередь не читает (очередь
    # докачки — title IS NULL, бэкфилл атрибутирует title IS NOT NULL;
    # пересечение пустое, см. честный разбор в docstring бэкфилла). После
    # миграции атрибуцию новым лотам даёт фаза выдачи (record_listing_shards
    # — для КАЖДОГО найденного id); лоты, минующие выдачу (первичный краул,
    # предикты пользователей), шард не получают — допустимо ровно до
    # появления потребителя детальных строк listing_shards: тогда версию
    # ключа поднять (v1 → v2) и переиграть.
    sentinel = "migrate:listing_shards_backfill:v1"
    done = conn.execute(
        "SELECT 1 FROM sweep_state WHERE key = ?", (sentinel,)
    ).fetchone()
    if done is None:
        # issue #152 (хвост ревью #169): сентинел — только по факту выполнения
        # бэкфилла. Раньше он записывался и при раннем выходе на легаси-схеме
        # (нет title/district/rooms — бэкфилл не исполнялся), и миграция
        # навсегда помечалась сделанной, хотя не делалась. None = «не смог»:
        # без сентинела следующий init_db (схема к тому моменту может быть
        # уже дотащена миграциями) попробует снова.
        backfilled = backfill_listing_shards_from_details(conn=conn)
        if backfilled is not None:
            conn.execute(
                "INSERT INTO sweep_state (key, value) VALUES (?, datetime('now'))",
                (sentinel,),
            )


def init_db(db_path: Path | str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def backfill_listing_shards_from_details(
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    """Бэкфилл атрибуции id → шард для лотов, у которых уже есть детали
    (issue #166, ревью): (district, rooms) однозначно определяют шард —
    фильтры выдачи непересекаются, — поэтому атрибуция из деталей точная,
    а не оценочная. INSERT OR IGNORE: свежая атрибуция от фазы выдачи
    (INSERT OR REPLACE в record_listing_shards) всегда выигрывает. Лоты без
    деталей (sighting-only backlog) здесь НЕ атрибутируются — их шард знает
    только выдача: они получают атрибуцию в проход, когда в ней встретятся.

    Возврат (issue #152): число записанных строк; None — схема не позволяет
    исполнить бэкфилл (гипер-легаси listings без title/district/rooms), чтобы
    вызывающий (_migrate) НЕ ставил сентинел миграции за работу, которая не
    выполнялась. 0 — исполнено честно, просто нечего атрибутировать:
    сентинел ставится.

    Честно про потребителя (issue #168): очередь докачки — `title IS NULL`,
    а бэкфилл отбирает `title IS NOT NULL` — пересечение пустое, и на состав
    дневной порции он не влияет НИКАК. Детальные строки listing_shards
    сегодня никто не читает; ценность бэкфилла — целостность карты «у лота
    с деталями есть шард», на которую сможет опереться будущий потребитель
    (контроль состава любой очереди, не только докачки), плюс то, что
    атрибуция из деталей — единственная, доступная без выдачи. Удалять уже
    записанные на проде строки смысла нет — схему #166 задача не трогает.

    Зовётся РАЗОВО, из _migrate под сентинелом sweep_state: до issue #168
    он выполнялся на каждом init_db (старт API, начало каждого прохода) —
    полным проходом по listings с десятками тысяч INSERT OR IGNORE, идемпо-
    тентным, но небесплатным. Остаётся публичным как разовый инструмент:
    вызов напрямую легален и идемпотентен.
    """
    with use_conn(conn, db_path) as conn:
        # Гипер-легаси схемы (миграции которых ещё не добавили title) —
        # бэкфилл невозможен: None, чтобы сентинел миграции не встал за
        # несделанную работу (issue #152), и повторная попытка при следующем
        # init_db осталась возможной.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
        if not {"title", "district", "rooms"} <= cols:
            return None
        rows = conn.execute(
            "SELECT id, district, rooms FROM listings "
            "WHERE title IS NOT NULL AND district IS NOT NULL AND rooms IS NOT NULL"
        ).fetchall()
        pairs = [
            (lid, label)
            for lid, district, rooms in rows
            if (label := listing_shard_label(district, rooms)) is not None
        ]
        if not pairs:
            return 0
        cur = conn.executemany(
            "INSERT OR IGNORE INTO listing_shards (listing_id, shard, seen_at) "
            "VALUES (?, ?, datetime('now'))",
            pairs,
        )
        return cur.rowcount


def listing_fingerprint(listing: dict[str, Any]) -> str | None:
    """«Отпечаток» квартиры для поиска дублей с разными id.

    Район + комнаты + площадь (до 0.5 м²) + этаж/этажность + координаты
    (~10 м). Перезалитое объявление почти наверняка даст тот же отпечаток.
    """
    area, lat, lon = listing.get("area"), listing.get("lat"), listing.get("lon")
    if not area or lat is None or lon is None:
        return None
    parts = (
        str(listing.get("district") or "").lower().strip(),
        str(listing.get("rooms") or ""),
        f"{round(float(area) * 2) / 2:.1f}",
        str(listing.get("floor") or ""),
        str(listing.get("total_floors") or ""),
        f"{float(lat):.4f}",
        f"{float(lon):.4f}",
    )
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def find_duplicate_id(
    fingerprint: str | None,
    exclude_id: int,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    """id другого объявления с тем же отпечатком (свежее — первым)."""
    if not fingerprint:
        return None
    with use_conn(conn, db_path) as conn:
        try:
            row = conn.execute(
                "SELECT id FROM listings WHERE fingerprint = ? AND id != ? "
                "ORDER BY is_active DESC, last_seen DESC LIMIT 1",
                (fingerprint, int(exclude_id)),
            ).fetchone()
        except sqlite3.OperationalError:  # старая база без колонки
            return None
    return int(row[0]) if row else None


SALE_PRICE_BOUNDS = (PRICE_MIN, PRICE_MAX)
RENT_PRICE_BOUNDS = (RENT_PRICE_MIN, RENT_PRICE_MAX)


def price_bounds_for(deal: str | None) -> tuple[int, int]:
    """Границы data-contract для цены по типу сделки.

    Продажа — ₸ за квартиру, аренда — ₸/месяц; один общий диапазон для них
    невозможен. Раньше контракт был захардкожен на продажу, и весь арендный
    рескрейп отбраковывал КАЖДУЮ цену: `krisha_rent.db` копилась с
    замороженными ценами и пустой price_history.
    """
    return RENT_PRICE_BOUNDS if deal == "arenda" else SALE_PRICE_BOUNDS


def is_valid_price(
    price: int | float | None, bounds: tuple[int, int] | None = None
) -> bool:
    """issue #103: data-contract на цену, для переиспользования вне
    upsert_listing — например rescrape.sweep()'s "знакомый лот, цена из
    карточки выдачи" путь, который пишет price в обход upsert_listing.

    `bounds` — (min, max) для нужного типа сделки (см. price_bounds_for);
    по умолчанию продажные, чтобы не менять поведение старых вызовов.
    """
    lo, hi = bounds or SALE_PRICE_BOUNDS
    return price is None or lo <= price <= hi


def record_parse_anomaly(
    conn: sqlite3.Connection, listing_id: int, field: str, reason: str, raw_value: Any
) -> None:
    conn.execute(
        "INSERT INTO parse_anomalies (listing_id, field, reason, raw_value) VALUES (?, ?, ?, ?)",
        (int(listing_id), field, reason, None if raw_value is None else str(raw_value)),
    )
    logger.warning(
        "listing %s: аномалия %s (%s), значение %r — в карантин, поле не записано",
        listing_id,
        field,
        reason,
        raw_value,
    )


def _validate_and_quarantine(
    conn: sqlite3.Connection,
    listing_id: int,
    row: dict[str, Any],
    price_bounds: tuple[int, int] | None = None,
) -> bool:
    """Data-contract проверка входа upsert_listing (issue #103): цена вне
    PRICE_MIN..MAX, площадь вне AREA_MIN..MAX или координаты вне ALMATY_BBOX —
    аудит в parse_anomalies + метрика качества парсинга, а не молча в базу.
    Возвращает True, если координаты (если были) прошли bbox-проверку.

    Не блокирует upsert целиком — только откатывает конкретное поле:
    - площадь — COALESCE-поле в UPSERT_SQL, `None` здесь = "не обновлять",
      старое хорошее значение (если было) остаётся;
    - цена — единственное из проверяемых полей в _UPSERT_ALWAYS (пишется
      безусловно), поэтому откатываем на текущее значение в БД явно, чтобы
      garbage-парс не затёр валидную цену NULL'ом/мусором;
    - координаты НЕ зануляем и не трогаем сырое значение в listings: train-time
      фильтр (`train._filter_stale_and_out_of_area`) уже сам исключает лоты
      вне ALMATY_BBOX, но специально НЕ исключает лоты без координат вовсе
      (их чинит resolve_zones дальше по пайплайну) — занулить здесь значило
      бы молча перевести "мусорные координаты" в "координат нет" и вернуть их
      обратно в train. Вместо этого anomaly просто логируется, а вызывающий
      (upsert_listing) использует возвращённый флаг, чтобы не пустить garbage
      координаты в fingerprint-дедуп/coords_approx.
    """
    price = row.get("price")
    # price is None тоже откатываем на сохранённое значение: `price` лежит в
    # _UPSERT_ALWAYS, то есть пишется безусловно (`price = excluded.price`),
    # а is_valid_price(None) → True, поэтому неполный парс (страница без
    # цены, «договорная») ЗАТИРАЛ хорошую цену NULL-ом. И залипало: на
    # следующем проходе _record_price_if_changed видит в истории ту же
    # цену, возвращает False, и UPDATE listings.price не происходит.
    if price is None or not is_valid_price(price, price_bounds):
        if price is not None:
            # Отсутствие цены — не аномалия парсинга, не засоряем метрику.
            record_parse_anomaly(conn, listing_id, "price", "out_of_range", price)
        prev = conn.execute(
            "SELECT price FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        row["price"] = int(prev[0]) if prev and prev[0] is not None else None

    area = row.get("area")
    if area is not None and not (AREA_MIN <= area <= AREA_MAX):
        record_parse_anomaly(conn, listing_id, "area", "out_of_range", area)
        row["area"] = None

    lat, lon = row.get("lat"), row.get("lon")
    if lat is not None and lon is not None and not (
        ALMATY_BBOX["lat_min"] <= lat <= ALMATY_BBOX["lat_max"]
        and ALMATY_BBOX["lon_min"] <= lon <= ALMATY_BBOX["lon_max"]
    ):
        record_parse_anomaly(conn, listing_id, "coords", "out_of_bbox", f"{lat},{lon}")
        return False
    return True


def _coords_approx(
    conn: sqlite3.Connection, listing_id: int, lat: float | None, lon: float | None
) -> int | None:
    """1 = координаты сидят на общей метке ЖК (issue #103): ≥ SHARED_PIN_MIN
    объявлений (включая это) на той же точке с округлением до 5 знаков
    (~1 м, тот же ROUND, что и снапшот scripts/fetch_osm_zones.py).

    `None`, если lat/lon в этом проходе не пришли или не прошли bbox-проверку
    — COALESCE в UPSERT_SQL тогда сохранит старое значение вместо ложного
    сброса в 0.
    """
    if lat is None or lon is None:
        return None
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE id != ? "
            "AND ROUND(lat, 5) = ROUND(?, 5) AND ROUND(lon, 5) = ROUND(?, 5)",
            (int(listing_id), lat, lon),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return None
    return 1 if (count + 1) >= SHARED_PIN_MIN else 0


def upsert_listing(
    listing: dict[str, Any],
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
    price_bounds: tuple[int, int] | None = None,
) -> None:
    row = {col: listing.get(col) for col in LISTING_COLUMNS}
    row["source"] = listing.get("source") or "scrape"
    is_user = row["source"] == "user"
    sql = UPSERT_SQL_USER if is_user else UPSERT_SQL
    with use_conn(conn, db_path) as conn:
        try:
            coords_valid = _validate_and_quarantine(conn, row["id"], row, price_bounds)
        except sqlite3.OperationalError:
            conn.executescript(SCHEMA)
            _migrate(conn)
            coords_valid = _validate_and_quarantine(conn, row["id"], row, price_bounds)
        # issue #103: fingerprint-дедуп и coords_approx считаем по "чистым"
        # координатам — garbage вне bbox не должен склеивать разные квартиры
        # по случайному совпадению битого парсинга и не должен засчитаться
        # как shared-pin ЖК. Сырое значение в row["lat"/"lon"] (то, что уйдёт
        # в listings) не трогаем — см. docstring _validate_and_quarantine.
        fp_source = row if coords_valid else {**row, "lat": None, "lon": None}
        row["fingerprint"] = listing_fingerprint(fp_source)
        row["coords_approx"] = (
            _coords_approx(conn, row["id"], row.get("lat"), row.get("lon")) if coords_valid else None
        )
        try:
            conn.execute(sql, row)
        except sqlite3.OperationalError:
            # старая база без новых колонок — мигрируем и пробуем ещё раз
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.execute(sql, row)
        # issue #117: пользовательский предикт не пишет точку в price_history
        # вовсе — иначе устаревшая/закэшированная страница ляжет в историю
        # как «изменение цены» и даст ложный алерт подписчикам /track.
        if not is_user and row.get("price") is not None:
            _record_price_if_changed(conn, row["id"], int(row["price"]))


def count_parse_anomalies(since: str | None = None, db_path: Path | str = DB_PATH) -> int:
    """Метрика качества парсинга (issue #103): сколько полей ушло в карантин.

    `since` — нижняя граница `detected_at` (например 'YYYY-MM-DD'), None — все.
    """
    with get_conn(db_path) as conn:
        try:
            if since:
                return conn.execute(
                    "SELECT COUNT(*) FROM parse_anomalies WHERE detected_at >= ?", (since,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM parse_anomalies").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


SIGHTING_UPSERT_SQL = """
INSERT INTO listings (id, url, price, source, first_seen, last_seen, is_active)
VALUES (:id, :url, :price, 'scrape', datetime('now'), datetime('now'), 1)
ON CONFLICT(id) DO UPDATE SET
    last_seen = datetime('now'),
    is_active = 1,
    delisted_at = NULL;
"""


def record_sighting(
    listing_id: int,
    url: str,
    price: int | None,
    db_path: Path | str = DB_PATH,
    price_bounds: tuple[int, int] | None = None,
) -> None:
    """Дешёвая запись «видели лот в выдаче» — без похода на детальную страницу.

    issue #127: рескрейп раньше писал в базу только первые `max_new_details`
    новых id за проход (в порядке обхода шардов) — остальные не получали
    даже `first_seen`, при притоке ~1000/день это накапливающийся backlog и
    смещение выборки. Теперь sighting пишется для КАЖДОГО найденного id сразу
    (этот вызов), а полный detail fetch остаётся отдельной лимитированной
    очередью, приоритет которой — самые старые ещё не докачанные лоты
    (`title IS NULL` — сентинел «сайтинг без детали», см. sweep()).
    """
    with get_conn(db_path) as conn:
        # issue #103: тот же data-contract, что и на пути upsert_listing.
        # Раньше его здесь не было вовсе — мусорная цена карточки для НОВОГО
        # id спокойно ложилась и в listings.price, и в price_history (для
        # знакомого id та же цена отбраковывалась), а count_parse_anomalies
        # при этом рапортовал чистый парс. Саму строку sighting пишем в любом
        # случае, просто без цены: иначе теряются first_seen и место в
        # очереди на докачку деталей — ровно то, ради чего заведён #127.
        if price is not None and not is_valid_price(price, price_bounds):
            try:
                record_parse_anomaly(conn, listing_id, "price", "out_of_range", price)
            except sqlite3.OperationalError:
                conn.executescript(SCHEMA)
                _migrate(conn)
                record_parse_anomaly(conn, listing_id, "price", "out_of_range", price)
            price = None
        try:
            conn.execute(SIGHTING_UPSERT_SQL, {"id": listing_id, "url": url, "price": price})
        except sqlite3.OperationalError:
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.execute(SIGHTING_UPSERT_SQL, {"id": listing_id, "url": url, "price": price})
        if price is not None:
            _record_price_if_changed(conn, listing_id, int(price))


def log_prediction(
    listing_id: Any,
    fair_price: float | None,
    fair_low: float | None,
    fair_high: float | None,
    verdict: str | None,
    model_version: str | None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Пишет строку в predictions (issue #128) — и для пользовательских, и для
    канальных (алерты) предиктов, единая точка входа `predict_from_listing`.

    Дублируется в долговечный лог (krisha.prediction_log): таблица уезжает
    вместе с базой, которую каждую ночь заново запекают в образ, поэтому в
    SQLite от рантайма не остаётся ни строки. Дубль дешёвый (JSON в репозитории
    раз в несколько минут) и не мешает: в Actions он сам себя выключает.
    """
    if listing_id is None:
        return
    from krisha import prediction_log

    prediction_log.record(
        listing_id, fair_price, fair_low, fair_high, verdict, model_version
    )
    with use_conn(conn, db_path) as conn:
        try:
            conn.execute(
                "INSERT INTO predictions "
                "(listing_id, fair_price, fair_low, fair_high, verdict, model_version) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(listing_id), fair_price, fair_low, fair_high, verdict, model_version),
            )
        except sqlite3.OperationalError:
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.execute(
                "INSERT INTO predictions "
                "(listing_id, fair_price, fair_low, fair_high, verdict, model_version) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(listing_id), fair_price, fair_low, fair_high, verdict, model_version),
            )


def _record_price_if_changed(conn: sqlite3.Connection, listing_id: int, price: int) -> bool:
    """Дописывает точку в price_history, если цена изменилась. True = записали.

    Скачок цены > PRICE_JUMP_ALERT_RATIO за одну точку — логируется как
    подозрительный (issue #98, second-line defence), но точка всё равно
    пишется: это может быть легитимный дисконт/ошибка объявления, а не только
    съехавший парсинг, и постоянно блокировать обновление — свой источник
    порчи данных (цена навсегда «зависнет» на устаревшем значении).
    """
    last = conn.execute(
        "SELECT price FROM price_history WHERE listing_id = ? ORDER BY observed_at DESC LIMIT 1",
        (listing_id,),
    ).fetchone()
    if last is not None and int(last[0]) == price:
        return False
    if last is not None and int(last[0]) > 0:
        jump = abs(price - int(last[0])) / int(last[0])
        if jump > PRICE_JUMP_ALERT_RATIO:
            logger.warning(
                "listing %s: подозрительный скачок цены %s → %s (%.0f%%)",
                listing_id,
                last[0],
                price,
                jump * 100,
            )
    conn.execute(
        "INSERT OR REPLACE INTO price_history (listing_id, price) VALUES (?, ?)",
        (listing_id, price),
    )
    return True


def get_price_history(
    listing_id: int,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """История цены объявления: [{price, observed_at}, ...] по возрастанию времени."""
    with use_conn(conn, db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT price, observed_at FROM price_history "
                "WHERE listing_id = ? ORDER BY observed_at",
                (listing_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [{"price": r[0], "observed_at": r[1]} for r in rows]


def upsert_complex(complex_row: dict[str, Any], db_path: Path | str = DB_PATH) -> None:
    row = {col: complex_row.get(col) for col in COMPLEX_COLUMNS}
    with get_conn(db_path) as conn:
        conn.execute(COMPLEX_UPSERT_SQL, row)


def known_complex_ids(db_path: Path | str = DB_PATH) -> set[int]:
    with get_conn(db_path) as conn:
        try:
            return {r[0] for r in conn.execute("SELECT id FROM complexes")}
        except sqlite3.OperationalError:
            return set()


def known_ids(db_path: Path | str = DB_PATH) -> set[int]:
    """Уже сохранённые id — чтобы не парсить повторно при рестарте краулера."""
    with get_conn(db_path) as conn:
        try:
            return {r[0] for r in conn.execute("SELECT id FROM listings")}
        except sqlite3.OperationalError:
            return set()


_SWEEP_RUN_COLUMNS = (
    "started_at", "deal", "found_in_search", "discovered_new", "details_fetched",
    "max_new_details", "max_new_effective", "detail_queue_after", "price_changes",
    "delisted", "failed_shards", "recovery_pass", "suspicious",
    # issue #152: тайминги фаз, фактические паузы и режим — для оценщика
    # плана прохода (pass_plan.estimate_timings) и гистерезиса режима.
    "search_pages", "search_seconds", "detail_requests", "detail_seconds",
    "delay_lo", "delay_hi", "mode",
)


def record_sweep_run(
    row: dict[str, Any], db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Пишет счётчики прохода в sweep_runs (issue #154).

    INSERT OR REPLACE по started_at. started_at пишется с миллисекундами
    (issue #171), поэтому перезаписывается только строка с ТОЧНО тем же
    моментом старта — то есть повторная запись того же прохода. Раньше ключ
    был секундной точности, и два прохода в одну секунду (ручной перезапуск
    джобы) схлопывались в одну строку.

    Ошибку глотаем: строка истории вторична, ронять из-за неё ночной проход
    (и терять вместе с ним заливку базы) — плохой размен.
    """
    payload = {c: row.get(c) for c in _SWEEP_RUN_COLUMNS}
    for flag in ("recovery_pass", "suspicious"):
        if payload[flag] is not None:
            payload[flag] = int(bool(payload[flag]))
    try:
        with use_conn(conn, db_path) as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO sweep_runs ({', '.join(_SWEEP_RUN_COLUMNS)}) "
                f"VALUES ({', '.join(':' + c for c in _SWEEP_RUN_COLUMNS)})",
                payload,
            )
    except sqlite3.Error:
        logger.warning("Не удалось записать историю прохода в sweep_runs", exc_info=True)


def recent_sweep_runs(
    limit: int = 7, deal: str = "prodazha", db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Последние проходы, свежие первыми. Пустой список, если таблицы ещё нет."""
    try:
        with use_conn(conn, db_path) as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_SWEEP_RUN_COLUMNS)} FROM sweep_runs "
                "WHERE COALESCE(deal, 'prodazha') = ? ORDER BY started_at DESC LIMIT ?",
                (deal, limit),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(zip(_SWEEP_RUN_COLUMNS, r)) for r in rows]


def count_listings(db_path: Path | str = DB_PATH) -> int:
    with get_conn(db_path) as conn:
        try:
            return conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


# ---------- issue #166: шардирование дневной порции докачки «район × комнаты» ----------


def record_listing_shards(
    pairs: list[tuple[int, str]],
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Атрибуция id → шард выдачи. Пишется фазой выдачи для каждого найденного
    id: INSERT OR REPLACE, поэтому лот, переехавший в другой шард (продавец
    отредактировал число комнат), переписывается на свежий шард сам."""
    if not pairs:
        return
    with use_conn(conn, db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO listing_shards (listing_id, shard, seen_at) "
            "VALUES (?, ?, datetime('now'))",
            pairs,
        )


def shard_cursors(
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> dict[str, int | None]:
    """Текущие водяные отметки шардов: {метка: last_id}. Пусто на холодной базе."""
    with use_conn(conn, db_path) as conn:
        try:
            rows = conn.execute("SELECT shard, last_id FROM shard_cursors").fetchall()
        except sqlite3.OperationalError:
            return {}
    return {r[0]: r[1] for r in rows}


def advance_shard_cursor(
    shard: str,
    last_id: int,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Сдвинуть водяную отметку шарда после успешной докачки окна."""
    with use_conn(conn, db_path) as conn:
        conn.execute(
            "INSERT INTO shard_cursors (shard, last_id, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(shard) DO UPDATE SET "
            "last_id = excluded.last_id, updated_at = excluded.updated_at",
            (shard, int(last_id)),
        )


def shard_backlog_window(
    conn: sqlite3.Connection,
    shard: str,
    cursor_id: int | None,
    limit: int,
) -> tuple[list[int], bool]:
    """Окно докачки одного шарда: не более `limit` id из backlog'а шарда.

    Порядок — круговой по убыванию id (id монотонен по времени публикации,
    свежие первыми — как решено в #152): сначала лоты НИЖЕ водяной отметки
    (cursor_id), при недоборе — добор с головы выдачи (wrap). Возвращает
    (ids, wrapped).

    Отметка по id, а не смещение: backlog между проходами меняется (докачанные
    и снятые уходят, свежие встают в голову), и offset «пропустил бы страницы»
    или качнул одни и те же лоты дважды. id-водяная отметка инвариантна к этим
    сдвигам: что докачано — выпало из backlog (title NOT NULL), что ниже
    отметки — будет взято следующими окнами.
    """
    if limit <= 0:
        return [], False
    base = (
        "SELECT l.id FROM listings l JOIN listing_shards s ON s.listing_id = l.id "
        "WHERE l.title IS NULL AND l.is_active = 1 AND s.shard = ?"
    )
    if cursor_id is None:
        rows = conn.execute(f"{base} ORDER BY l.id DESC LIMIT ?", (shard, limit)).fetchall()
        return [r[0] for r in rows], False
    below = [
        r[0]
        for r in conn.execute(
            f"{base} AND l.id < ? ORDER BY l.id DESC LIMIT ?", (shard, cursor_id, limit)
        ).fetchall()
    ]
    if len(below) >= limit:
        return below, False
    head = [
        r[0]
        for r in conn.execute(
            f"{base} AND l.id > ? ORDER BY l.id DESC LIMIT ?",
            (shard, cursor_id, limit - len(below)),
        ).fetchall()
    ]
    return below + head, bool(head)


def shard_backlog_count(conn: sqlite3.Connection, shard: str | None = None) -> int:
    """Сколько лотов ждут докачки деталей: по шарду или всего (shard=None).

    Всего — включая лоты без атрибуции (не видены в выдаче с момента ввода
    схемы; ждут переобнаружения или delisted — из очереди не берутся).
    """
    if shard is None:
        return conn.execute(
            "SELECT COUNT(*) FROM listings WHERE title IS NULL AND is_active = 1"
        ).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM listings l JOIN listing_shards s ON s.listing_id = l.id "
        "WHERE l.title IS NULL AND l.is_active = 1 AND s.shard = ?",
        (shard,),
    ).fetchone()[0]


def unattributed_backlog_count(conn: sqlite3.Connection) -> int:
    """Лоты backlog'а без атрибуции к шарду (не встречались в выдаче с момента
    ввода схемы). Наблюдаемость: при штатной работе стремится к нулю."""
    return conn.execute(
        "SELECT COUNT(*) FROM listings l "
        "LEFT JOIN listing_shards s ON s.listing_id = l.id "
        "WHERE l.title IS NULL AND l.is_active = 1 AND s.listing_id IS NULL"
    ).fetchone()[0]


def last_known_shard_stock(conn: sqlite3.Connection) -> dict[str, int]:
    """Последний измеренный сток выдачи по каждому шарду (из sweep_shard_stats).

    Фолбэк для квоты шарда, не покрытого текущим проходом (бан/таймаут/сеть):
    его backlog всё ещё надо разгребать, а размер выдачи мы знаем с прошлых
    проходов. Шард без единого успешного замера получает квоту 0 — выдумывать
    ему равную долю значило бы перекосить порцию в пользу неизвестного шарда.
    """
    try:
        rows = conn.execute(
            """
            SELECT s1.shard, s1.stock FROM sweep_shard_stats s1
            WHERE s1.stock IS NOT NULL AND s1.run_seq = (
                SELECT MAX(s2.run_seq) FROM sweep_shard_stats s2
                WHERE s2.shard = s1.shard AND s2.stock IS NOT NULL
            )
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: r[1] for r in rows}


def zero_quota_streak(
    conn: sqlite3.Connection,
    shard: str,
    run_seq: int,
    limit: int,
) -> int:
    """Сколько из последних `limit` записанных проходов (строго до run_seq)
    шард провёл с непустым backlog'ом и нулевой квотой — подряд, начиная со
    свежего (issue #168).

    Смотрит только на записанные проходы: убитый до итогов запуск квоты не
    раздавал и backlog не дренировал — он не «нулевой проход» шарда, его
    просто нет. Пропуски номеров run_seq (ранний инкремент счётчика, #168)
    на подсчёт не влияют: берутся последние СТРОКИ, а не последние номера.

    issue #152: проходы с нулевым потолком докачки (диагностика --max-new 0,
    pass_cap = 0) из подсчёта исключены: там quota=0 у ВСЕХ шардов сразу —
    это режим прохода, а не заморозка конкретного шарда; диагностический
    проход ни наращивает серию, ни обрывает её. NULL pass_cap (строки до
    #152) читаем как обычный проход с ненулевым потолком — тогда диагностики
    в принципе не было.
    """
    rows = conn.execute(
        "SELECT quota, backlog_before FROM sweep_shard_stats "
        "WHERE shard = ? AND run_seq < ? AND COALESCE(pass_cap, 1) > 0 "
        "ORDER BY run_seq DESC LIMIT ?",
        (shard, run_seq, limit),
    ).fetchall()
    streak = 0
    for quota, backlog in rows:
        if (quota or 0) == 0 and (backlog or 0) > 0:
            streak += 1
        else:
            break
    return streak


def record_sweep_shard_stats(
    rows: list[dict[str, Any]],
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> None:
    """План/факт докачки по шардам за проход (issue #166). 32 строки в сутки.

    Как и record_sweep_run: ошибку глотаем — наблюдаемость вторична, ронять
    из-за неё ночной проход нельзя.
    """
    if not rows:
        return
    cols = (
        "run_seq", "shard", "started_at", "stock", "quota", "fetched",
        "backlog_before", "backlog_after", "cursor_after", "wrapped", "pass_cap",
    )
    payload = [
        {**{c: r.get(c) for c in cols}, "wrapped": int(bool(r.get("wrapped")))}
        for r in rows
    ]
    try:
        with use_conn(conn, db_path) as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO sweep_shard_stats ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})",
                payload,
            )
    except sqlite3.Error:
        logger.warning("Не удалось записать план/факт по шардам в sweep_shard_stats", exc_info=True)


def sweep_pass_seq(conn: sqlite3.Connection, deal: str) -> int:
    """Сырой монотонный номер прохода (issue #166).

    Порядок обхода шардов ротируется между запусками, чтобы обрыв прохода
    (бюджет времени, бан) не ампутировал систематически один и тот же хвост
    алфавита: «день» перестаёт означать «район». Состояние — явный счётчик в
    sweep_state (монотонен, в отличие от COUNT(sweep_runs) с его историческим
    PK); живёт в базе, которая едет релизом на раннер и обратно. Смещение
    ротации из номера вычисляет shard_plan.rotation_offset; сам номер идёт в
    run_seq sweep_shard_stats (там started_at секундной точности не подходит
    для сортировки — схлопывает перезапуски в одну секунду)."""
    try:
        row = conn.execute(
            "SELECT value FROM sweep_state WHERE key = ?", (f"pass_seq:{deal}",)
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def advance_sweep_pass_seq(conn: sqlite3.Connection, deal: str) -> int:
    """+1 к номеру прохода и возврат нового значения (issue #168).

    Зовётся в НАЧАЛЕ прохода, отдельной транзакцией, до фазы выдачи. До этой
    правки инкремент был в конце, в одной транзакции с итогами: мягкий
    дедлайн (--time-budget-min) проход завершал и счётчик ехал, а жёсткий
    kill раннера (timeout-minutes рубит job вместе с транзакцией) оставлял
    номер прежним — следующий запуск повторял то же смещение ротации и
    ампутировал тот же хвост шардов. Причина обрыва систематическая, значит
    и перекос был систематическим — ровно то, от чего уходили шагом 13.
    Цена раннего инкремента — пропуск номера при падении прохода на старте:
    номера не обязаны быть плотными, от них нужна только монотонность
    (источник ротации и run_seq в sweep_shard_stats)."""
    key = f"pass_seq:{deal}"
    conn.execute(
        "INSERT INTO sweep_state (key, value, updated_at) VALUES (?, '1', datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET "
        "value = CAST(CAST(value AS INTEGER) + 1 AS TEXT), "
        "updated_at = excluded.updated_at",
        (key,),
    )
    row = conn.execute("SELECT value FROM sweep_state WHERE key = ?", (key,)).fetchone()
    return int(row[0])


def consecutive_bans(conn: sqlite3.Connection, deal: str) -> int:
    """Сколько подряд проходов завершились BanDetected (issue #152).

    Состояние — в sweep_state, а не выводится из sweep_runs: записи о проходе
    могут не быть (жёсткий kill раннера до итогов), а серия банов — ровно тот
    сигнал, который нельзя терять вместе с убитым проходом. Ключ per deal:
    бан арендного прохода не должен откатывать продажный (разные базы,
    разные расписания, общий IP — но решение об откате принимает каждый
    проход по своей истории).
    """
    row = conn.execute(
        "SELECT value FROM sweep_state WHERE key = ?", (f"ban_streak:{deal}",)
    ).fetchone()
    return int(row[0]) if row else 0


def record_consecutive_bans(conn: sqlite3.Connection, deal: str, streak: int) -> None:
    """Записать серию банов после прохода: +1 при бане, 0 при чистом проходе."""
    conn.execute(
        "INSERT INTO sweep_state (key, value, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET "
        "value = excluded.value, updated_at = excluded.updated_at",
        (f"ban_streak:{deal}", str(int(streak))),
    )


def last_sweep_mode(conn: sqlite3.Connection, deal: str) -> str | None:
    """Режим последнего прохода (drain|steady) из sweep_runs — для гистерезиса
    choose_mode (issue #152). NULL у старых строк (режим появился в #152)
    пропускаем: «прежнего режима» у них не существовало. None, если истории
    нет вообще — тогда choose_mode между порогами берёт steady по умолчанию.
    """
    try:
        row = conn.execute(
            "SELECT mode FROM sweep_runs WHERE COALESCE(deal, 'prodazha') = ? "
            "AND mode IS NOT NULL ORDER BY started_at DESC LIMIT 1",
            (deal,),
        ).fetchone()
    except sqlite3.OperationalError:  # таблицы ещё нет (холодная база)
        return None
    return row[0] if row else None


def remember_update_id(update_id: int, db_path: Path | str = DB_PATH, keep: int = 5000) -> bool:
    """True — апдейт видим впервые, False — уже обрабатывали.

    Общая для всех воркеров память о телеграм-апдейтах (в самом процессе есть
    ещё быстрый deque — сюда доходят только те, кого он не отсеял). Fail-soft:
    если базы нет или она недоступна на запись, возвращаем True — лучше
    редкий дубль ответа, чем молчащий бот.
    """
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tg_updates ("
                "update_id INTEGER PRIMARY KEY, seen_at TEXT DEFAULT (datetime('now')))"
            )
            cur = conn.execute(
                "INSERT OR IGNORE INTO tg_updates (update_id) VALUES (?)", (int(update_id),)
            )
            if cur.rowcount == 0:
                return False
            # Чистим хвост, чтобы таблица не росла: держим последние `keep`.
            conn.execute(
                "DELETE FROM tg_updates WHERE update_id < ("
                "SELECT MIN(update_id) FROM (SELECT update_id FROM tg_updates "
                "ORDER BY update_id DESC LIMIT ?))",
                (keep,),
            )
            return True
    except sqlite3.Error:
        logger.warning("tg_updates недоступна — дедуп только в памяти", exc_info=True)
        return True
