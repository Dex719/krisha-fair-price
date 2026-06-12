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


def init_db(db_path: Path | str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_listing(listing: dict[str, Any], db_path: Path | str = DB_PATH) -> None:
    row = {col: listing.get(col) for col in LISTING_COLUMNS}
    with get_conn(db_path) as conn:
        conn.execute(UPSERT_SQL, row)


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
