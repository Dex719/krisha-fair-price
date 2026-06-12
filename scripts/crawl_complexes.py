"""Разовый скрейп каталога ЖК (этап 2 роадмапа).

Источник URL — сайтмап https://krisha.kz/sitemap/frontend/complexes.xml
(полный список страниц /complex/show/...). Берём ссылки с регионом almaty
плюс ссылки без региона в URL (регион уточняем после парсинга страницы).
В базу пишем только ЖК с region «Алматы».

Запуск:  python scripts/crawl_complexes.py [--limit N] [--all-regions]
В конце обновляет снапшот models/complexes.json для деплоя.
"""

import argparse
import logging
import re
import sys

from krisha.complexes import normalize_complex_name, snapshot_complexes
from krisha.config import DB_PATH
from krisha.db import init_db, known_complex_ids, upsert_complex
from krisha.scraping.client import PoliteClient
from krisha.scraping.complex_parser import parse_complex

SITEMAP_URL = "https://krisha.kz/sitemap/frontend/complexes.xml"
LOC_RE = re.compile(r"<loc>(https://krisha\.kz/complex/show/[^<]+)</loc>")
ALMATY_REGION = "Алматы"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def sitemap_urls(client: PoliteClient, all_regions: bool) -> list[str]:
    xml = client.get(SITEMAP_URL)
    if not xml:
        logger.error("Не смогли скачать сайтмап ЖК")
        return []
    urls = LOC_RE.findall(xml)
    if all_regions:
        return urls
    # almaty в пути + ссылки без региона (/complex/show/{slug}/ — регион узнаем со страницы)
    picked = [
        u for u in urls
        if "almaty" in u.lower() or re.fullmatch(r"https://krisha\.kz/complex/show/[^/]+/?", u)
    ]
    logger.info("Сайтмап: %s ссылок всего, %s взяли в работу", len(urls), len(picked))
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description="Скрейп каталога ЖК Krisha")
    parser.add_argument("--limit", type=int, default=0, help="максимум страниц (0 = без лимита)")
    parser.add_argument("--all-regions", action="store_true", help="не фильтровать по Алматы")
    parser.add_argument("--skip-known", action="store_true", help="пропускать уже собранные ЖК")
    args = parser.parse_args()

    init_db(DB_PATH)
    known = known_complex_ids(DB_PATH) if args.skip_known else set()

    saved = skipped = failed = 0
    with PoliteClient() as client:
        urls = sitemap_urls(client, args.all_regions)
        if args.limit:
            urls = urls[: args.limit]
        for i, url in enumerate(urls, 1):
            html = client.get(url)
            if not html:
                failed += 1
                continue
            row = parse_complex(html, url)
            if not row:
                failed += 1
                continue
            if row["id"] in known:
                skipped += 1
                continue
            if not args.all_regions and row.get("region") != ALMATY_REGION:
                skipped += 1
                continue
            row["name_norm"] = normalize_complex_name(row.get("name"))
            upsert_complex(row, DB_PATH)
            saved += 1
            if i % 50 == 0:
                logger.info("%s/%s | сохранено %s, мимо региона/дубли %s, ошибок %s",
                            i, len(urls), saved, skipped, failed)

    n = snapshot_complexes(DB_PATH)
    logger.info("Готово: сохранено %s ЖК, пропущено %s, ошибок %s; снапшот %s записей",
                saved, skipped, failed, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
