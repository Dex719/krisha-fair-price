"""Парсер страницы ЖК (/complex/show/...): застройщик, класс жилья, срок сдачи и пр.

Источники на странице:
- window.data.complex — JSON: id, name, address, region, координаты;
- блоки <dt data-name="...">/<dd> — класс жилья, этажность, тип дома, отделка...;
- сайдбар complex__sidebar-info — застройщик, срок сдачи, статус строительства.

Проверено на реальных страницах в июне 2026.
"""

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

WINDOW_DATA_RE = re.compile(r"window\.data\s*=\s*(\{.*?\});", re.S)
YEAR_RE = re.compile(r"(20\d{2})")
FLOORS_RE = re.compile(r"(\d+)(?:\s*-\s*(\d+))?\s*этаж")

# dt data-name → имя колонки в таблице complexes
PARAM_MAP = {
    "housingClass": "housing_class",
    "count.of.floors": "floors_raw",
    "home.type": "material",
    "facing.type": "facing",
    "count.of.apartments": "apartments_count",
    "ceilings.height": "ceiling_raw",
}


def _extract_window_complex(html: str) -> dict[str, Any]:
    m = WINDOW_DATA_RE.search(html)
    if not m:
        return {}
    try:
        return json.loads(m.group(1)).get("complex") or {}
    except json.JSONDecodeError:
        logger.warning("window.data на странице ЖК не парсится")
        return {}


def _extract_params(soup: BeautifulSoup) -> dict[str, str]:
    """<dt data-name="housingClass">…</dt><dd>комфорт</dd> → dict."""
    params: dict[str, str] = {}
    for dt in soup.select("dt.complex-parameters__block-title[data-name]"):
        dd = dt.find_next_sibling("dd")
        if dd:
            params[dt["data-name"]] = dd.get_text(" ", strip=True)
    return params


def _extract_sidebar(soup: BeautifulSoup) -> dict[str, str]:
    """Сайдбар: «Застройщик» → KazSMU, «Срок сдачи» → …, «Статус строительства» → …"""
    info: dict[str, str] = {}
    for block in soup.select(".complex__sidebar-info"):
        title = block.select_one(".complex__sidebar-info-title")
        text = block.select_one(".complex__sidebar-info-text")
        if title and text:
            info[title.get_text(strip=True)] = text.get_text(" ", strip=True)
    return info


def parse_completion_year(deadline_text: str | None) -> int | None:
    """«Первая очередь - III квартал 2025 г. Вторая - IV квартал 2026 г.» → 2026 (последняя)."""
    if not deadline_text:
        return None
    years = [int(y) for y in YEAR_RE.findall(deadline_text)]
    return max(years) if years else None


def parse_max_floors(floors_text: str | None) -> int | None:
    """«7-9 этажей» → 9, «12 этажей» → 12."""
    if not floors_text:
        return None
    m = FLOORS_RE.search(floors_text)
    if not m:
        return None
    return int(m.group(2) or m.group(1))


def parse_complex(html: str, url: str = "") -> dict[str, Any] | None:
    """HTML страницы ЖК → плоский dict для таблицы complexes (см. db.COMPLEX_COLUMNS)."""
    data = _extract_window_complex(html)
    if not data or not data.get("id"):
        logger.warning("Не нашли window.data.complex: %s", url)
        return None

    soup = BeautifulSoup(html, "lxml")
    params = _extract_params(soup)
    sidebar = _extract_sidebar(soup)

    deadline = sidebar.get("Срок сдачи")
    coords = data.get("map") or {}
    apartments = params.get("count.of.apartments")

    return {
        "id": int(data["id"]),
        "url": url,
        "name": data.get("name"),
        "region": data.get("region"),
        "address": data.get("address"),
        "developer": sidebar.get("Застройщик"),
        "housing_class": (params.get("housingClass") or "").strip().lower() or None,
        "completion_year": parse_completion_year(deadline),
        "deadline_text": deadline,
        "construction_status": sidebar.get("Статус строительства"),
        "material": (params.get("home.type") or "").strip().lower() or None,
        "max_floors": parse_max_floors(params.get("count.of.floors")),
        "facing": params.get("facing.type"),
        "apartments_count": int(apartments) if apartments and apartments.isdigit() else None,
        "lat": coords.get("lat"),
        "lon": coords.get("lon"),
        "raw_params": json.dumps({**params, **sidebar}, ensure_ascii=False),
    }
