#!/usr/bin/env python
"""CLI: сбор объявлений в SQLite.

Примеры:
    python scripts/crawl.py --pages 5 --limit 50   # быстрая проба
    python scripts/crawl.py --pages 300            # полный сбор (~6000 объявлений)
"""

import argparse
import logging

from krisha.scraping.crawler import crawl


def main() -> None:
    parser = argparse.ArgumentParser(description="Краулер Krisha.kz → SQLite")
    parser.add_argument("--pages", type=int, default=50, help="Максимум страниц выдачи")
    parser.add_argument("--limit", type=int, default=None, help="Максимум новых объявлений")
    parser.add_argument("--no-skip", action="store_true", help="Перепарсить уже известные id")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    crawl(max_pages=args.pages, max_listings=args.limit, skip_known=not args.no_skip)


if __name__ == "__main__":
    main()
