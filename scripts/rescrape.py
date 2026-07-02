#!/usr/bin/env python
"""CLI этапа 4: регулярный рескрейп — история цен, дни на рынке, новые объявления.

Выдача обходится по шардам «район × комнаты» (32 шт.), --pages — лимит
страниц на один шард.

Примеры:
    python scripts/rescrape.py                  # полный проход всех шардов
    python scripts/rescrape.py --pages 3        # быстрая проба (по 3 стр. на шард)
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from krisha.scraping.rescrape import sweep  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Рескрейп Krisha.kz: цены и ликвидность")
    parser.add_argument("--pages", type=int, default=250, help="Максимум страниц выдачи на один шард")
    parser.add_argument("--max-new", type=int, default=300, help="Максимум новых детальных страниц")
    parser.add_argument("--summary-json", help="Записать счётчики прохода в JSON-файл")
    parser.add_argument(
        "--fail-empty",
        action="store_true",
        help="Выйти с кодом 1, если выдача пуста (блокировка/сетевая ошибка) — "
        "чтобы CI-запуск был явно красным, а не тихо закоммитил пустой проход",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stats = sweep(max_pages=args.pages, max_new_details=args.max_new)

    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(stats, ensure_ascii=False, indent=2))

    if args.fail_empty and stats["found_in_search"] == 0:
        logging.error("Выдача пуста — вероятно, блокировка по IP или разметка изменилась")
        sys.exit(1)


if __name__ == "__main__":
    main()
