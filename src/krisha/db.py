"""SQLite-хранилище объявлений. Одна таблица `listings`, upsert по id."""

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from krisha.config import DB_PATH

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

UPSERT_SQL = """
INSERT INTO listings (
    id, url, title, price, rooms, area, floor, total_floors, building_type,
    year_built, ceiling, district, microdistrict, street, house_num,
    address_title, complex_name, lat, lon, user_type, category, description,
    photos_count, raw_params, source, fingerprint, first_seen, last_seen, is_active
) VALUES (
    :id, :url, :title, :price, :rooms, :area, :floor, :total_floors, :building_type,
    :year_built, :ceiling, :district, :microdistrict, :street, :house_num,
    :address_title, :complex_name, :lat, :lon, :user_type, :category, :description,
    :photos_count, :raw_params, :source, :fingerprint, datetime('now'), datetime('now'), 1
)
ON CONFLICT(id) DO UPDATE SET
    price = excluded.price,
    title = excluded.title,
    raw_params = excluded.raw_params,
    fingerprint = excluded.fingerprint,
    last_seen = datetime('now'),
    is_active = 1,
    delisted_at = NULL,
    scraped_at = datetime('now');
"""

LISTING_COLUMNS = [
    "id", "url", "title", "price", "rooms", "area", "floor", "total_floors",
    "building_type", "year_built", "ceiling", "district", "microdistrict",
    "street", "house_num", "address_title", "complex_name", "lat", "lon",
    "user_type", "category", "description", "photos_count", "raw_params",
]


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
    with get_conn(db_path) as conn:
        try:
            conn.execute(UPSERT_SQL, row)
        except sqlite3.OperationalError:
            # старая база без новых колонок — мигрируем и пробуем ещё раз
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.execute(UPSERT_SQL, row)
        if row.get("price") is not None:
            _record_price_if_changed(conn, row["id"], int(row["price"]))


def _record_price_if_changed(conn: sqlite3.Connection, listing_id: int, price: int) -> bool:
    """Дописывает точку в price_history, если цена изменилась. True = записали."""
    last = conn.execute(
        "SELECT price FROM price_history WHERE listing_id = ? ORDER BY observed_at DESC LIMIT 1",
        (listing_id,),
    ).fetchone()
    if last is not None and int(last[0]) == price:
        return False
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
