"""issue #152: смоук scripts/dedup_stats.py — пересчёт дедуп-статистики по
fingerprint на текущей базе (нужен после разгребания backlog'а: доля дублей
на неполном составе и на полном различается, метрику #129 надо перепроверить).

Скрипт read-only: повторяет train-пайплайн до шага дедупликации. Тест
проверяет, что на базе с заведомым перезаливом он находит ровно его, и что
в базу он ничего не пишет.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

from krisha.db import init_db, upsert_listing

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dedup_stats.py"
_spec = importlib.util.spec_from_file_location("dedup_stats", _SCRIPT_PATH)
dedup_stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dedup_stats)


def _listing(lid: int, price: int) -> dict:
    # Реалистичные значения: координаты внутри Алматы (иначе карантин #103),
    # цена внутри price_bounds, чтобы clean() строки не выкинул.
    return {
        "id": lid,
        "url": f"https://krisha.kz/a/show/{lid}",
        "title": "2-комнатная квартира",
        "price": price,
        "rooms": 2,
        "area": 50.0,
        "floor": 3,
        "total_floors": 9,
        "district": "Bostandykskiy_r-n",
        "lat": 43.238,
        "lon": 76.945,
        "is_active": 1,
    }


def test_dedup_stats_finds_relisting_and_writes_nothing(tmp_path, monkeypatch, capsys):
    db = tmp_path / "t.db"
    init_db(db)
    # Перезалив: одна квартира, два id, цена та же ±копейки → fingerprint-дубль.
    upsert_listing(_listing(101, 15_000_000), db)
    upsert_listing(_listing(102, 15_200_000), db)
    # Контроль: другая квартира (другой этаж) — дублем НЕ считается.
    other = {**_listing(103, 15_000_000), "floor": 5}
    upsert_listing(other, db)
    with sqlite3.connect(db) as conn:
        before = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    monkeypatch.setattr(sys, "argv", ["dedup_stats.py", "--db", str(db)])
    dedup_stats.main()
    out = capsys.readouterr().out

    assert "Строк до дедупа: 3" in out
    assert "Строк после:     2" in out
    assert "Дублей:          1" in out
    # read-only: состав базы не изменился
    with sqlite3.connect(db) as conn:
        after = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    assert after == before
