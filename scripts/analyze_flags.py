"""Пакетный LLM-анализ описаний (этап 5): заполняет кэш llm_flags.

Берёт объявления с описанием, для которых нет свежего кэша, и гонит их
через Gemini пачками. Бережно к лимитам бесплатного тарифа: пауза между
запросами + ретраи на 429 внутри analyze_batch.

  GEMINI_API_KEY=...  python scripts/analyze_flags.py [--limit N] [--batch 30]

Прогресс печатается каждым батчем; скрипт можно прерывать и перезапускать —
готовое из кэша не переанализируется.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from krisha.config import DB_PATH  # noqa: E402
from krisha.db import get_conn  # noqa: E402
from krisha.llm_flags import (  # noqa: E402
    CACHE_SCHEMA,
    GEMINI_API_KEY_ENV,
    analyze_batch,
    desc_hash,
    save_flags,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("analyze_flags")

BATCH_DELAY_S = float(os.environ.get("LLM_BATCH_DELAY", "7"))  # ~8 запросов/мин


def pending_listings(limit: int | None) -> list[tuple[int, str]]:
    """(id, description) без актуального кэша; активные — первыми."""
    with get_conn(DB_PATH) as conn:
        conn.executescript(CACHE_SCHEMA)
        rows = conn.execute(
            "SELECT l.id, l.description, f.desc_hash AS cached_hash "
            "FROM listings l LEFT JOIN llm_flags f ON f.listing_id = l.id "
            "WHERE l.description IS NOT NULL AND LENGTH(TRIM(l.description)) >= 20 "
            "ORDER BY l.is_active DESC, l.id DESC"
        ).fetchall()
    items = [
        (row["id"], row["description"])
        for row in rows
        if row["cached_hash"] != desc_hash(row["description"])
    ]
    return items[:limit] if limit else items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="максимум объявлений")
    parser.add_argument("--batch", type=int, default=30, help="описаний на запрос")
    args = parser.parse_args()

    if not os.environ.get(GEMINI_API_KEY_ENV):
        logger.error("Нет %s в окружении", GEMINI_API_KEY_ENV)
        return 1

    items = pending_listings(args.limit)
    if not items:
        logger.info("Кэш актуален, анализировать нечего")
        return 0
    logger.info("К анализу: %d описаний, батч %d", len(items), args.batch)

    done = failed = 0
    flag_counts: dict[str, int] = {}
    for i in range(0, len(items), args.batch):
        chunk = items[i : i + args.batch]
        result = analyze_batch(chunk)
        if result is None:
            failed += len(chunk)
            logger.warning("Батч %d: запрос не удался, пропускаю", i // args.batch + 1)
        else:
            for lid, text in chunk:
                flags = result.get(lid)
                if flags is None:  # модель потеряла id — не кэшируем, доберём позже
                    failed += 1
                    continue
                save_flags(lid, text, flags)
                done += 1
                for f in flags:
                    flag_counts[f] = flag_counts.get(f, 0) + 1
        logger.info("Прогресс: %d/%d (ошибок %d)", done, len(items), failed)
        if i + args.batch < len(items):
            time.sleep(BATCH_DELAY_S)

    logger.info("Готово: %d проанализировано, %d не удалось", done, failed)
    logger.info("Частоты флагов: %s", json.dumps(flag_counts, ensure_ascii=False))
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
