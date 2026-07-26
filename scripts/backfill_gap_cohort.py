#!/usr/bin/env python
"""Разовая ретро-разметка когорты восстановительного прохода (issue #156).

Зачем отдельный скрипт, а не правило в _migrate().

Механизм из #161 помечает когорту тем проходом, который её создаёт. Но
восстановительный проход АРЕНДЫ случился 26.07.2026 в 16:04–18:00 UTC, на
коммите, где механизма ещё не было: воркфлоu стартовал в 16:04, а #161
смержился в 17:58. Волна из 5031 лота получила first_seen = 26.07 без метки,
а к следующему проходу разрыв составит 0.94 дня — ниже порога 2.5, то есть
автодетект уже никогда не сработает.

Соблазнительно дописать в _migrate() автодетект «осиротевшего» прохода:
найти пачку снятий с аномальным лагом delisted_at - last_seen и пометить
лоты, появившиеся в тот же день. Так делать НЕЛЬЗЯ. Такое правило сработает
и на базе продажи, где есть точно такая же сигнатура от провала 13.06–01.07
(1919 снятий разом 02.07, лаг ≈ 21 день) — а ту когорту помечать нельзя:
из-за лимита --max-new 1000/день бэкфилл размазался по всем двенадцати
последующим дням, и отделить его от органики наблюдением невозможно.
Пометить весь период — выбросить почти весь органический датасет.

Разница между двумя случаями не выводится из данных, она в том, ЧТО МЫ
ЗНАЕМ про каждый инцидент. Поэтому это не инвариант, а разовый ремонт с
явными границами, зафиксированный в истории запусков.

Границы окна берутся из логов Actions (время работы прохода), а gap_start
выводится из самой базы — это последнее наблюдение перед провалом.

Идемпотентно: предикат first_seen_cohort IS NULL и INSERT OR IGNORE, так
что повторный запуск ничего не меняет.

Пример:
    python scripts/backfill_gap_cohort.py --db data/krisha_rent.db \\
        --window-start "2026-07-26 16:04:00" \\
        --window-end   "2026-07-26 18:01:00" \\
        --expect 5031 --dry-run
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from krisha.db import init_db  # noqa: E402


def backfill(
    db_path: str,
    window_start: str,
    window_end: str,
    expect: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Помечает когортой лоты, впервые увиденные в окне восстановительного прохода.

    Возвращает счётчики. `expect` — сколько лотов ожидается в окне; при
    несовпадении поднимает SystemExit. Это защита от запуска по чужой базе
    или с неверным окном: молча пометить не ту когорту хуже, чем не пометить.
    """
    init_db(db_path)  # гарантируем, что колонка и таблица есть
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Последнее наблюдение ПЕРЕД провалом. Выводим из базы, а не
        # принимаем аргументом: снятые лоты сохраняют свой last_seen (delist
        # его не трогает), поэтому максимум по ним и есть граница провала.
        gap_start = conn.execute(
            "SELECT MAX(last_seen) FROM listings WHERE last_seen < ? "
            "AND COALESCE(source, 'scrape') <> 'user'",
            (window_start,),
        ).fetchone()[0]
        if gap_start is None:
            raise SystemExit(
                "Не нашлось ни одного наблюдения до начала окна — "
                "похоже, окно задано неверно или база не та"
            )

        candidates = conn.execute(
            "SELECT COUNT(*) FROM listings "
            "WHERE first_seen >= ? AND first_seen < ? "
            "AND first_seen_cohort IS NULL "
            "AND COALESCE(source, 'scrape') <> 'user'",
            (window_start, window_end),
        ).fetchone()[0]

        gap_days = conn.execute(
            "SELECT julianday(?) - julianday(?)", (window_start, gap_start)
        ).fetchone()[0]
        cohort = f"gap:{str(gap_start)[:10]}"

        print(f"база              : {db_path}")
        print(f"окно прохода      : {window_start} .. {window_end}")
        print(f"последнее до него : {gap_start}")
        print(f"длина провала     : {gap_days:.2f} дн.")
        print(f"метка когорты     : {cohort}")
        print(f"лотов под пометку : {candidates}")

        if expect is not None and candidates != expect:
            raise SystemExit(
                f"ОЖИДАЛОСЬ {expect} лотов, найдено {candidates} — не совпало. "
                f"Проверьте базу и границы окна; ничего не изменено"
            )

        if dry_run:
            print("\n--dry-run: ничего не записано")
            return {"gap_start": gap_start, "cohort": cohort, "marked": 0,
                    "candidates": candidates}

        conn.execute(
            "INSERT OR IGNORE INTO data_gaps (gap_start, gap_end, note) VALUES (?, ?, ?)",
            (gap_start, window_start,
             "retro: восстановительный проход прошёл до появления механизма (#156)"),
        )
        marked = conn.execute(
            "UPDATE listings SET first_seen_cohort = ? "
            "WHERE first_seen >= ? AND first_seen < ? "
            "AND first_seen_cohort IS NULL "
            "AND COALESCE(source, 'scrape') <> 'user'",
            (cohort, window_start, window_end),
        ).rowcount
        conn.commit()
        print(f"\nпомечено          : {marked}")
        return {"gap_start": gap_start, "cohort": cohort, "marked": marked,
                "candidates": candidates}
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, help="Путь к базе")
    p.add_argument("--window-start", required=True,
                   help="Начало окна восстановительного прохода, 'YYYY-MM-DD HH:MM:SS' UTC")
    p.add_argument("--window-end", required=True, help="Конец окна (не включая)")
    p.add_argument("--expect", type=int,
                   help="Ожидаемое число лотов в окне — защита от чужой базы")
    p.add_argument("--dry-run", action="store_true", help="Показать и не записывать")
    args = p.parse_args()
    backfill(args.db, args.window_start, args.window_end, args.expect, args.dry_run)


if __name__ == "__main__":
    main()
