#!/usr/bin/env python
"""issue #152: пересчёт статистики дедупликации по fingerprint на текущей базе.

Доля дублей зависит от покрытия: на неполном составе (26 322 лота с деталями
на 02.08) она одна (10.51%), на полном (~40k) — другая. Метрика нужна после
разгребания backlog'а (переход drain → steady сигналит об этом в отчёте),
чтобы проверить, что дедуп #129 с DEDUP_PRICE_TOLERANCE не схлопывает живые
квартиры сильнее/слабее, чем на старом составе.

Скрипт НИЧЕГО не пишет в базу: повторяет train-пайплайн ровно до шага
дедупликации (load_dataset → clean → resolve_zones → dedup_relistings) и
печатает статистику. Те же цифры пишет в лог и обычный retrain — этот
скрипт просто способ получить их без обучения.

Примеры:
    python scripts/dedup_stats.py                  # data/krisha.db
    python scripts/dedup_stats.py --db /path/to/krisha.db
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from krisha.config import DB_PATH  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH), help="Путь к базе (по умолчанию продажная)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from krisha.features import clean
    from krisha.train import dedup_relistings, load_dataset
    from krisha.zones import resolve_zones

    df = load_dataset(args.db)
    df = clean(df)
    df = resolve_zones(df)
    before = len(df)
    df = dedup_relistings(df)
    stats = df.attrs.get("dedup_stats", {})

    after = len(df)
    dropped = before - after
    share = 100 * dropped / before if before else 0.0
    print(f"\nСтрок до дедупа: {before}")
    print(f"Строк после:     {after}")
    print(f"Дублей:          {dropped} ({share:.2f}%)")
    print(f"Fingerprint-групп с дублями: {stats.get('fingerprint_dup_groups', '?')}")
    print(
        "Доля новостроек среди групп: "
        f"{stats.get('new_building_share_of_dup_groups', '?')}"
    )
    hist = stats.get("fingerprint_group_size_histogram") or {}
    if hist:
        print("Размеры групп (размер: сколько групп):", dict(sorted(hist.items())))


if __name__ == "__main__":
    main()
