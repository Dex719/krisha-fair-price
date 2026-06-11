"""Парсер страницы объявления Krisha.kz.

Главный источник данных — встроенный JSON `window.data = {...};` (там advert:
id, price, square, rooms, address, координаты). Дополнительные параметры
(этаж, год постройки, тип дома, потолки) — из HTML-блоков с data-name.

Проверено на реальных страницах в июне 2026. Если Krisha поменяет вёрстку,
чинить в первую очередь PARAMS-селекторы, window.data обычно стабилен.
"""

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

WINDOW_DATA_RE = re.compile(r"window\.data\s*=\s*(\{.*?\});", re.S)
FLOOR_TITLE_RE = re.compile(r"(\d+)/(\d+)\s*этаж")  # "4/5 этаж" в title
FLOOR_PARAM_RE = re.compile(r"(\d+)\s*из\s*(\d+)")  # "4 из 5" в параметрах
FIRST_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _first_number(text: str | None) -> float | None:
    if not text:
        return None
    m = FIRST_NUMBER_RE.search(text.replace("\xa0", " "))
    return float(m.group(0).replace(",", ".")) if m else None


def extract_window_data(html: str) -> dict[str, Any] | None:
    m = WINDOW_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        logger.warning("window.data найден, но JSON не парсится")
        return None


def extract_params(soup: BeautifulSoup) -> dict[str, str]:
    """Все параметры объявления вида data-name → текст значения.

    Krisha использует два формата:
    1. <div data-name="flat.floor">...<div class="offer__advert-short-info">4 из 5</div></div>
    2. <dt data-name="ceiling">...</dt><dd>2.8 м</dd>
    """
    params: dict[str, str] = {}
    for el in soup.select("[data-name]"):
        name = el["data-name"]
        if el.name == "dt":
            dd = el.find_next_sibling("dd")
            if dd:
                params[name] = dd.get_text(" ", strip=True)
        else:
            info = el.select_one(".offer__advert-short-info")
            if info:
                params[name] = info.get_text(" ", strip=True)
    return params


def _parse_floor(params: dict[str, str], title: str) -> tuple[int | None, int | None]:
    raw = params.get("flat.floor", "")
    m = FLOOR_PARAM_RE.search(raw) or FLOOR_TITLE_RE.search(title)
    if m:
        return int(m.group(1)), int(m.group(2))
    # бывает указан только этаж без этажности
    n = _first_number(raw)
    return (int(n), None) if n else (None, None)


def parse_detail(html: str, url: str = "") -> dict[str, Any] | None:
    """HTML страницы объявления → плоский dict для записи в БД (см. db.LISTING_COLUMNS)."""
    data = extract_window_data(html)
    if not data or "advert" not in data:
        logger.warning("Не нашли window.data.advert: %s", url)
        return None
    adv = data["advert"]
    if not adv.get("id"):
        return None

    soup = BeautifulSoup(html, "lxml")
    params = extract_params(soup)
    title = adv.get("title") or ""
    floor, total_floors = _parse_floor(params, title)

    address = adv.get("address") or {}
    coords = adv.get("map") or {}

    desc_el = soup.select_one(".offer__description .text") or soup.select_one(
        "div.js-description"
    )

    year = _first_number(params.get("house.year"))
    ceiling = _first_number(params.get("ceiling"))
    area = adv.get("square") or _first_number(params.get("live.square"))

    return {
        "id": int(adv["id"]),
        "url": url or f"https://krisha.kz/a/show/{adv['id']}",
        "title": title,
        "price": int(adv["price"]) if adv.get("price") else None,
        "rooms": int(adv["rooms"]) if adv.get("rooms") else None,
        "area": float(area) if area else None,
        "floor": floor,
        "total_floors": total_floors,
        "building_type": params.get("flat.building"),
        "year_built": int(year) if year else None,
        "ceiling": ceiling,
        "district": address.get("district"),
        "microdistrict": address.get("microdistrict"),
        "street": address.get("street"),
        "house_num": address.get("house_num"),
        "address_title": adv.get("addressTitle"),
        "complex_name": adv.get("complexName"),
        "lat": coords.get("lat"),
        "lon": coords.get("lon"),
        "user_type": adv.get("userType"),
        "category": adv.get("categoryAlias"),
        "description": desc_el.get_text(" ", strip=True) if desc_el else None,
        "photos_count": len(adv.get("photos") or []),
        "raw_params": json.dumps(params, ensure_ascii=False),
    }
