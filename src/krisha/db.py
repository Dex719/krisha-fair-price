"""SQLite-хранилище объявлений. Одна таблица `listings`, upsert по id."""

import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from krisha.config import DB_PATH

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
    c for c in [*LISTING_COLUMNS, "fingerprint"]
    if c != "id" and c not in _UPSERT_ALWAYS
]

_UPSERT_SET = ", ".join(
    [f"{c} = excluded.{c}" for c in _UPSERT_ALWAYS]
    + [f"{c} = COALESCE(excluded.{c}, {c})" for c in _UPSERT_COALESCE]
)

UPSERT_SQL = f"""
INSERT INTO listings (
    {", ".join(LISTING_COLUMNS)},
    source, fingerprint, first_seen, last_seen, is_active
) VALUES (
    {", ".join(":" + c for c in LISTING_COLUMNS)},
    :source, :fingerprint, datetime('now'), datetime('now'), 1
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
    for c in [*LISTING_COLUMNS, "fingerprint"]
    if c not in ("id", "price", "title", "raw_params")
)

UPSERT_SQL_USER = f"""
INSERT INTO listings (
    {", ".join(LISTING_COLUMNS)},
    source, fingerprint, first_seen, last_seen, is_active
) VALUES (
    {", ".join(":" + c for c in LISTING_COLUMNS)},
    :source, :fingerprint, datetime('now'), datetime('now'), 1
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
    except sqlite3.OperationalError:
        pass  # read-only ФС и т.п. — работаем как раньше
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Этап 4: новые колонки listings для старых баз (CREATE IF NOT EXISTS их не добавит)
_MIGRATION_COLUMNS = {
    "first_seen": "TEXT",
    "last_seen": "TEXT",
    "is_active": "INTEGER DEFAULT 1",
    "delisted_at": "TEXT",
    # Пользовательские объявления: откуда запись и «отпечаток» квартиры для дублей
    "source": "TEXT DEFAULT 'scrape'",
    "fingerprint": "TEXT",
}


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    for col, decl in _MIGRATION_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {decl}")
    # Индекс здесь, а не в SCHEMA: колонка fingerprint появляется миграцией.
    # Без него find_duplicate_id() делает full-scan на каждый предикт.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_listings_fingerprint ON listings(fingerprint)"
    )
    # Бэкфилл: для старых записей точка отсчёта — момент скрейпа
    conn.execute("UPDATE listings SET first_seen = scraped_at WHERE first_seen IS NULL")
    conn.execute("UPDATE listings SET last_seen = scraped_at WHERE last_seen IS NULL")
    conn.execute("UPDATE listings SET is_active = 1 WHERE is_active IS NULL")
    # Стартовая точка истории цены для всех объявлений без истории
    conn.execute(
        """INSERT OR IGNORE INTO price_history (listing_id, price, observed_at)
           SELECT id, price, first_seen FROM listings
           WHERE price IS NOT NULL
             AND id NOT IN (SELECT DISTINCT listing_id FROM price_history)"""
    )


def init_db(db_path: Path | str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


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
    fingerprint: str | None, exclude_id: int, db_path: Path | str = DB_PATH
) -> int | None:
    """id другого объявления с тем же отпечатком (свежее — первым)."""
    if not fingerprint:
        return None
    with get_conn(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT id FROM listings WHERE fingerprint = ? AND id != ? "
                "ORDER BY is_active DESC, last_seen DESC LIMIT 1",
                (fingerprint, int(exclude_id)),
            ).fetchone()
        except sqlite3.OperationalError:  # старая база без колонки
            return None
    return int(row[0]) if row else None


def upsert_listing(listing: dict[str, Any], db_path: Path | str = DB_PATH) -> None:
    row = {col: listing.get(col) for col in LISTING_COLUMNS}
    row["source"] = listing.get("source") or "scrape"
    row["fingerprint"] = listing_fingerprint(listing)
    is_user = row["source"] == "user"
    sql = UPSERT_SQL_USER if is_user else UPSERT_SQL
    with get_conn(db_path) as conn:
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


SIGHTING_UPSERT_SQL = """
INSERT INTO listings (id, url, price, source, first_seen, last_seen, is_active)
VALUES (:id, :url, :price, 'scrape', datetime('now'), datetime('now'), 1)
ON CONFLICT(id) DO UPDATE SET
    last_seen = datetime('now'),
    is_active = 1,
    delisted_at = NULL;
"""


def record_sighting(
    listing_id: int, url: str, price: int | None, db_path: Path | str = DB_PATH
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
) -> None:
    """Пишет строку в predictions (issue #128) — и для пользовательских, и для
    канальных (алерты) предиктов, единая точка входа `predict_from_listing`.
    """
    if listing_id is None:
        return
    with get_conn(db_path) as conn:
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


def get_price_history(listing_id: int, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """История цены объявления: [{price, observed_at}, ...] по возрастанию времени."""
    with get_conn(db_path) as conn:
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


def count_listings(db_path: Path | str = DB_PATH) -> int:
    with get_conn(db_path) as conn:
        try:
            return conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        except sqlite3.OperationalError:
            return 0
