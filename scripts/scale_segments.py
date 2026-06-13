#!/usr/bin/env python
"""Масштабирование базы: сегментированный обход выдачи + переобучение.

Старый `scale_db.py` ходил по одному запросу `prodazha/kvartiry/almaty/` и
упирался в дедупликацию (выдача всегда отдаёт верхний срез). Здесь обходим
много срезов (район × комнатность), каждый отдаёт свой набор объявлений.

Запуск: PYTHONPATH=src python scripts/scale_segments.py --target 20000
Возобновляемый (skip_known) — можно прерывать и перезапускать.
"""

import argparse
import json
import logging
from pathlib import Path

from krisha.db import count_listings
from krisha.scraping.crawler import build_segments, crawl_segments
from krisha.train import train


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=20000, help="Целевой размер базы")
    parser.add_argument("--max-pages", type=int, default=500, help="Макс. страниц на срез")
    parser.add_argument("--no-train", action="store_true", help="Только сбор, без переобучения")
    parser.add_argument("--result", default="/tmp/scale_segments_result.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("scale_segments")

    have = count_listings()
    need = args.target - have
    log.info("В базе %s, цель %s, надо добрать %s", have, args.target, max(0, need))
    if need > 0:
        crawl_segments(
            build_segments(),
            max_pages_per_segment=args.max_pages,
            max_listings=need,
            skip_known=True,
        )

    total = count_listings()
    log.info("Сбор завершён: %s объявлений.", total)

    result = {"total_listings": total, "metrics": None}
    if not args.no_train:
        log.info("Переобучаем модель…")
        result["metrics"] = train()
        m = result["metrics"]["model"]
        log.info("Готово: MAPE %.2f%% R² %.3f", m["mape"] * 100, m["r2"])

    Path(args.result).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
