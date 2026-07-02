"""Зоны Алматы из OpenStreetMap: полигоны районов и микрорайонов (Overpass API).

Зачем: krisha отдаёт district/microdistrict из своей карты зон, и они часто
пустые (микрорайон отсутствует у ~57% объявлений) или не совпадают с
координатами. Здесь собираем честные границы из OSM:

- 8 городских районов Алматы (admin_level=6) — полигоны;
- микрорайоны (place=suburb/neighbourhood/quarter) — полигоны, привязанные к
  меткам krisha по большинству размеченных объявлений внутри полигона;
- центроиды меток микрорайонов по базе объявлений — фолбэк, когда полигона нет;
- «общие пины» — координаты, на которых сидит много объявлений (метка ЖК,
  а не конкретный дом) — для бейджа «координаты примерные».

Результат — снапшот models/osm_zones.json (коммитится, как osm_pois.json).
Запуск:  python scripts/fetch_osm_zones.py
"""

import json
import logging
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from krisha.config import DB_PATH, OSM_ZONES_SNAPSHOT_PATH  # noqa: E402
from krisha.zones import SHARED_PIN_MIN, point_in_polygon  # noqa: E402

# Overpass отвечает 406 на браузерные UA — нужен честный UA инструмента
OSM_USER_AGENT = "krisha-fair-price/1.0 (research project; one-off zones fetch)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fetch_osm_zones")

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
BBOX = "43.10,76.70,43.45,77.15"  # south,west,north,east — как в fetch_osm_pois

# name:ru района в OSM → ключ района krisha (как в БД и DISTRICT_RU)
OSM_DISTRICT_TO_KEY = {
    "Алмалинский район": "Almalinskiy_r-n",
    "Алатауский район": "Alatauskiy_r-n",
    "Ауэзовский район": "Auezovskiy_r-n",
    "Бостандыкский район": "Bostandykskiy_r-n",
    "Жетысуский район": "Zhetysuskiy_r-n",
    "Медеуский район": "Medeuskiy_r-n",
    "Наурызбайский район": "Nauryzbayskiy_r-n",
    "Турксибский район": "Turksibskiy_r-n",
}

# Привязка OSM-полигона микрорайона к метке krisha: минимум размеченных
# объявлений внутри и доля большинства, чтобы не привязать мусор.
MICRO_MATCH_MIN_LISTINGS = 3
MICRO_MATCH_MIN_SHARE = 0.6
ROUND = 5  # знаков после запятой в координатах снапшота (~1 м)


def overpass(query: str) -> dict:
    body = f"[out:json][timeout:180];{query}"
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        for attempt in range(3):
            try:
                resp = httpx.post(
                    url, data={"data": body},
                    headers={"User-Agent": OSM_USER_AGENT}, timeout=240,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 — ретраим любой сбой зеркала
                last_err = exc
                wait = 10 * (attempt + 1)
                logger.warning("Overpass %s: %s — повтор через %d с", url, exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"Overpass недоступен: {last_err}")


def _close_enough(a: tuple, b: tuple) -> bool:
    return abs(a[0] - b[0]) < 1e-7 and abs(a[1] - b[1]) < 1e-7


def assemble_rings(ways: list[list[tuple[float, float]]]) -> list[list[list[float]]]:
    """Сшивает отрезки way в замкнутые кольца (граница района — набор way)."""
    segments = [list(w) for w in ways if len(w) >= 2]
    rings: list[list[list[float]]] = []
    while segments:
        chain = segments.pop()
        progress = True
        while progress and not _close_enough(chain[0], chain[-1]):
            progress = False
            for i, seg in enumerate(segments):
                if _close_enough(chain[-1], seg[0]):
                    chain += seg[1:]
                elif _close_enough(chain[-1], seg[-1]):
                    chain += seg[-2::-1]
                elif _close_enough(chain[0], seg[-1]):
                    chain = seg[:-1] + chain
                elif _close_enough(chain[0], seg[0]):
                    chain = seg[::-1][:-1] + chain
                else:
                    continue
                segments.pop(i)
                progress = True
                break
        if len(chain) >= 4 and _close_enough(chain[0], chain[-1]):
            rings.append([[round(lat, ROUND), round(lon, ROUND)] for lat, lon in chain])
        else:
            logger.warning("Незамкнутое кольцо из %d точек — пропускаю", len(chain))
    return rings


def relation_polygon(el: dict) -> dict | None:
    """Relation с геометрией → {"outer": [кольца], "inner": [кольца]}."""
    by_role: dict[str, list] = defaultdict(list)
    for m in el.get("members", []):
        if m.get("type") == "way" and m.get("geometry"):
            role = m.get("role") or "outer"
            by_role[role].append([(g["lat"], g["lon"]) for g in m["geometry"]])
    outer = assemble_rings(by_role.get("outer", []))
    if not outer:
        return None
    return {"outer": outer, "inner": assemble_rings(by_role.get("inner", []))}


def way_polygon(el: dict) -> dict | None:
    """Замкнутый way с геометрией → полигон без дырок."""
    geom = el.get("geometry") or []
    if len(geom) < 4:
        return None
    ring = [[round(g["lat"], ROUND), round(g["lon"], ROUND)] for g in geom]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"outer": [ring], "inner": []}


def fetch_districts() -> list[dict]:
    data = overpass(
        f'relation["boundary"="administrative"]["admin_level"="6"]({BBOX});out geom;'
    )
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name_ru = tags.get("name:ru") or tags.get("name") or ""
        key = OSM_DISTRICT_TO_KEY.get(name_ru)
        if key is None:
            continue  # соседние области/город Алатау — не районы Алматы
        poly = relation_polygon(el)
        if poly is None:
            logger.warning("Район %s: не удалось собрать полигон", name_ru)
            continue
        out.append({"key": key, "name_ru": name_ru, "polygon": poly})
        logger.info("Район %s → %s: %d внешних колец", name_ru, key, len(poly["outer"]))
    missing = set(OSM_DISTRICT_TO_KEY.values()) - {d["key"] for d in out}
    if missing:
        raise RuntimeError(f"Не собраны районы: {missing}")
    return out


def fetch_micro_polygons() -> list[dict]:
    data = overpass(
        f'nwr["place"~"^(suburb|neighbourhood|quarter)$"]({BBOX});out geom;'
    )
    out = []
    for el in data.get("elements", []):
        name = (el.get("tags") or {}).get("name:ru") or (el.get("tags") or {}).get("name")
        poly = None
        if el["type"] == "way":
            poly = way_polygon(el)
        elif el["type"] == "relation":
            poly = relation_polygon(el)
        if poly is not None:
            out.append({"osm_name": name, "polygon": poly})
    logger.info("Микрорайоны: %d полигонов из OSM", len(out))
    return out


def load_labeled_listings() -> list[tuple[float, float, str | None]]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT lat, lon, microdistrict FROM listings "
            "WHERE lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchall()
    return rows


def match_micro_labels(polygons: list[dict], listings: list[tuple]) -> list[dict]:
    """Метка krisha для каждого OSM-полигона — по большинству объявлений внутри."""
    matched = []
    for item in polygons:
        labels = Counter(
            micro for lat, lon, micro in listings
            if micro and point_in_polygon(lat, lon, item["polygon"])
        )
        if not labels:
            continue
        label, n = labels.most_common(1)[0]
        total = sum(labels.values())
        if n >= MICRO_MATCH_MIN_LISTINGS and n / total >= MICRO_MATCH_MIN_SHARE:
            matched.append({"label": label, "osm_name": item["osm_name"],
                            "polygon": item["polygon"]})
    logger.info("Микрорайоны: %d полигонов привязано к меткам krisha", len(matched))
    return matched


def micro_centroids(listings: list[tuple]) -> dict[str, list[float]]:
    """Медианный центр каждой метки микрорайона по базе — фолбэк без полигона."""
    pts: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for lat, lon, micro in listings:
        if micro:
            pts[micro].append((lat, lon))
    out = {}
    for label, coords in pts.items():
        lats = sorted(c[0] for c in coords)
        lons = sorted(c[1] for c in coords)
        mid = len(coords) // 2
        out[label] = [round(lats[mid], ROUND), round(lons[mid], ROUND), len(coords)]
    logger.info("Центроиды: %d меток микрорайонов", len(out))
    return out


def shared_pins() -> list[list[float]]:
    """Координаты, на которых сидит ≥ SHARED_PIN_MIN объявлений (метка ЖК)."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            f"SELECT ROUND(lat, {ROUND}), ROUND(lon, {ROUND}), COUNT(*) c FROM listings "
            f"WHERE lat IS NOT NULL AND lon IS NOT NULL "
            f"GROUP BY 1, 2 HAVING c >= ? ORDER BY c DESC",
            (SHARED_PIN_MIN,),
        ).fetchall()
    logger.info("Общие пины (≥%d объявлений): %d координат", SHARED_PIN_MIN, len(rows))
    return [[lat, lon, c] for lat, lon, c in rows]


def main() -> None:
    districts = fetch_districts()
    time.sleep(2)
    micro_polys = fetch_micro_polygons()
    listings = load_labeled_listings()
    snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "bbox": BBOX,
        "districts": districts,
        "microdistricts": match_micro_labels(micro_polys, listings),
        "micro_centroids": micro_centroids(listings),
        "shared_pins": shared_pins(),
    }
    OSM_ZONES_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OSM_ZONES_SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    logger.info("Готово → %s", OSM_ZONES_SNAPSHOT_PATH)


if __name__ == "__main__":
    main()
