"""SQLite-хранилище объявлений. Одна таблица `listings`, upsert по id."""

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
    scraped_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_listings_district ON listings(district);
CREATE INDEX IF NOT EXISTS idx_listings_rooms ON listings(rooms);
"""

UPSERT_SQL = """
INSERT INTO listings (
    id, url, title, price, rooms, area, floor, total_floors, building_type,
    year_built, ceiling, district, microdistrict, street, house_num,
    address_title, complex_name, lat, lon, user_type, category, description,
    photos_count, raw_params
) VALUES (
    :id, :url, :title, :price, :rooms, :area, :floor, :total_floors, :building_type,
    :year_built, :ceiling, :district, :microdistrict, :street, :house_num,
    :address_title, :complex_name, :lat, :lon, :user_type, :category, :description,
    :photos_count, :raw_params
)
ON CONFLICT(id) DO UPDATE SET
    price = excluded.price,
    title = excluded.title,
    raw_params = excluded.raw_params,
    scraped_at = datetime('now');
"""

LISTING_COLUMNS = [
    "id", "url", "title", "price", "rooms", "area", "floor", "total_floors",
    "building_type", "year_built", "ceiling", "district", "microdistrict",
    "street", "house_num", "address_title", "complex_name", "lat", "lon",
    "user_type", "category", "description", "photos_count", "raw_params",
]


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


def init_db(db_path: Path | str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_listing(listing: dict[str, Any], db_path: Path | str = DB_PATH) -> None:
    row = {col: listing.get(col) for col in LISTING_COLUMNS}
    with get_conn(db_path) as conn:
        conn.execute(UPSERT_SQL, row)


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
