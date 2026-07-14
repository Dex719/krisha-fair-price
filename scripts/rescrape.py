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

from krisha.config import DB_PATH, RENT_DB_PATH  # noqa: E402
from krisha.scraping.rescrape import sweep  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Рескрейп Krisha.kz: цены и ликвидность")
    parser.add_argument("--pages", type=int, default=250, help="Максимум страниц выдачи на один шард")
    parser.add_argument("--max-new", type=int, default=300, help="Максимум новых детальных страниц")
    parser.add_argument(
        "--refresh-stale-days",
        type=int,
        default=30,
        help="issue #102: активные лоты с деталями старше N дней с последнего "
        "scraped_at докачиваются повторно (площадь/этаж/описание/координаты, "
        "отредактированные продавцом, иначе никогда не обновляются)",
    )
    parser.add_argument(
        "--max-refresh",
        type=int,
        default=300,
        help="Максимум повторных детальных докачек устаревших активных лотов за проход "
        "(0 — выключить обновление устаревших деталей)",
    )
    parser.add_argument(
        "--deal",
        choices=["prodazha", "arenda"],
        default="prodazha",
        help="prodazha — продажа (data/krisha.db), arenda — долгосрочная аренда "
        "(отдельная база data/krisha_rent.db, цена = ₸/мес)",
    )
    parser.add_argument("--summary-json", help="Записать счётчики прохода в JSON-файл")
    parser.add_argument(
        "--fail-empty",
        action="store_true",
        help="Выйти с кодом 1, если выдача пуста, помечена подозрительной (просевший "
        "parse-rate против медианы последних 7 проходов) или ниже --fail-below — "
        "чтобы CI-запуск был явно красным (и не заливал db-latest поверх релиза), "
        "а не тихо закоммитил битый/пустой проход",
    )
    parser.add_argument(
        "--fail-below",
        type=int,
        default=0,
        help="Абсолютный минимум найденных в выдаче объявлений (например 20000): "
        "ниже — та же обработка, что и --fail-empty. 0 — проверка выключена",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = RENT_DB_PATH if args.deal == "arenda" else DB_PATH
    stats = sweep(
        max_pages=args.pages,
        max_new_details=args.max_new,
        db_path=db_path,
        deal=args.deal,
        refresh_stale_days=args.refresh_stale_days,
        max_refresh=args.max_refresh,
    )

    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(stats, ensure_ascii=False, indent=2))

    if args.fail_below and stats["found_in_search"] < args.fail_below:
        logging.error(
            "В выдаче %s объявлений — ниже порога --fail-below %s",
            stats["found_in_search"],
            args.fail_below,
        )
        sys.exit(1)

    if args.fail_empty:
        if stats["found_in_search"] == 0:
            logging.error("Выдача пуста — вероятно, блокировка по IP или разметка изменилась")
            sys.exit(1)
        if stats.get("banned"):
            logging.error(
                "Проход прерван досрочно — похоже на бан (серия HTTP 403) — "
                "не заливаем db-latest"
            )
            sys.exit(1)
        if stats.get("suspicious"):
            logging.error(
                "Проход помечен подозрительным (parse-rate просел против медианы %s "
                "последних) — не заливаем db-latest",
                stats.get("parse_rate_median_7"),
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
