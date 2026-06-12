#!/usr/bin/env python
"""CLI этапа 4: регулярный рескрейп — история цен, дни на рынке, новые объявления.

Примеры:
    python scripts/rescrape.py                  # полный проход выдачи
    python scripts/rescrape.py --pages 20       # быстрая проба
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from krisha.scraping.rescrape import sweep  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Рескрейп Krisha.kz: цены и ликвидность")
    parser.add_argument("--pages", type=int, default=400, help="Максимум страниц выдачи")
    parser.add_argument("--max-new", type=int, default=300, help="Максимум новых детальных страниц")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sweep(max_pages=args.pages, max_new_details=args.max_new)


if __name__ == "__main__":
    main()
