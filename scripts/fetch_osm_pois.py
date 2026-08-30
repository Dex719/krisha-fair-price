"""Этап 3 роадмапа: разовая выгрузка POI Алматы из OpenStreetMap (Overpass API).

Категории: метро, школы, детсады, парки, супермаркеты, остановки,
крупные дороги (motorway/trunk/primary) и промзоны (landuse=industrial).
Полигоны и линии прореживаются до точек с шагом ~60 м.

Результат — снапшот models/osm_pois.json (коммитится в репо, как complexes.json):
    {"fetched_at": ..., "pois": {"metro": [[lat, lon], ...], ...}}

Запуск:  python scripts/fetch_osm_pois.py
"""

import json
import logging
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from krisha.config import OSM_POIS_SNAPSHOT_PATH  # noqa: E402

# Overpass отвечает 406 на браузерные UA — нужен честный UA инструмента
OSM_USER_AGENT = "krisha-fair-price/1.0 (research project; one-off POI fetch)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fetch_osm_pois")

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
# Алматы с запасом: юг гор не нужен, охватываем город и пригороды
BBOX = "43.10,76.70,43.45,77.15"  # south,west,north,east

# Категория → Overpass-фильтр. nwr = node+way+relation, out center даёт центроид.
QUERIES = {
    "metro": '(node["station"="subway"]({bbox});node["railway"="subway_entrance"]({bbox}););',
    "school": 'nwr["amenity"="school"]({bbox});',
    "kindergarten": 'nwr["amenity"="kindergarten"]({bbox});',
    "park": 'nwr["leisure"="park"]({bbox});',
    "supermarket": 'nwr["shop"="supermarket"]({bbox});',
    "bus_stop": 'node["highway"="bus_stop"]({bbox});',
    "big_road": 'way["highway"~"^(motorway|trunk|primary)$"]({bbox});',
    "industrial": 'way["landuse"="industrial"]({bbox});',
    # Строящееся метро: тоннели продления до «Калкаман» (railway=construction)
    # + проектная трасса «Продление до Барлык». Близость будущей станции
    # закладывается в цену до запуска (рыночная разведка, авг 2026).
    "metro_construction": (
        '(way["railway"="construction"]({bbox});'
        'way["railway"="proposed"]["name"~"Барлык"]({bbox}););'
    ),
}
GEOMETRY_CATS = {"big_road", "industrial", "metro_construction"}  # линии/полигоны → прореженные вершины
STEP_KM = 0.06  # шаг прореживания геометрии


def overpass(query: str) -> dict:
    body = f"[out:json][timeout:120];{query}"
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        for attempt in range(3):
            try:
                resp = httpx.post(
                    url, data={"data": body},
                    headers={"User-Agent": OSM_USER_AGENT}, timeout=180,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 — ретраим любой сбой зеркала
                last_err = exc
                wait = 10 * (attempt + 1)
                logger.warning("Overpass %s: %s — повтор через %d с", url, exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"Overpass недоступен: {last_err}")


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def decimate(points: list[tuple[float, float]], step_km: float = STEP_KM) -> list[tuple[float, float]]:
    """Прореживает полилинию: оставляет точки не чаще, чем раз в step_km."""
    out: list[tuple[float, float]] = []
    for lat, lon in points:
        if not out or _haversine_km(out[-1][0], out[-1][1], lat, lon) >= step_km:
            out.append((lat, lon))
    return out


def extract_points(cat: str, elements: list[dict]) -> list[list[float]]:
    points: list[list[float]] = []
    for el in elements:
        if cat in GEOMETRY_CATS:
            geom = el.get("geometry") or []
            for lat, lon in decimate([(g["lat"], g["lon"]) for g in geom]):
                points.append([round(lat, 5), round(lon, 5)])
        elif "lat" in el:
            points.append([round(el["lat"], 5), round(el["lon"], 5)])
        elif "center" in el:
            points.append([round(el["center"]["lat"], 5), round(el["center"]["lon"], 5)])
    return points


def main() -> None:
    pois: dict[str, list[list[float]]] = {}
    for cat, q in QUERIES.items():
        query = q.format(bbox=BBOX)
        out_mode = "out geom;" if cat in GEOMETRY_CATS else "out center;"
        data = overpass(f"{query}{out_mode}")
        pois[cat] = extract_points(cat, data.get("elements", []))
        logger.info("%s: %d точек", cat, len(pois[cat]))
        time.sleep(2)

    snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "bbox": BBOX,
        "pois": pois,
    }
    OSM_POIS_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OSM_POIS_SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    total = sum(len(v) for v in pois.values())
    logger.info("Готово: %d точек → %s", total, OSM_POIS_SNAPSHOT_PATH)


if __name__ == "__main__":
    main()
