"""issue #115: analogs/alerts/daily_report открывали sqlite3.connect напрямую,
мимо db.get_conn (без WAL/busy_timeout) — на хот-пасе (analogs — каждый
/api/predict) это било по конкурентной записи с `database is locked`.

Регрессионный тест: новые прямые `sqlite3.connect(...)` вне db.py не должны
появляться незамеченными. Модули из `_PRE_EXISTING_ALLOWLIST` — уже
существовавший до #115 долг вне скоупа этого issue (батч/оффлайн-код, не
хот-пас запроса); не расширяй список без явной причины в комментарии рядом.
"""

import re
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "krisha"

_CONNECT_RE = re.compile(r"sqlite3\.connect\(")

# Пути относительно SRC_DIR. Все читают/пишут не на пути /api/predict —
# батч-скрипты обучения и статистики, а не конкурентный API-хендлер.
_PRE_EXISTING_ALLOWLIST = {
    "db.py",  # тут и живёт get_conn — единственное легитимное место
    "stats.py",
    "train.py",
    "complexes.py",
}


def test_no_new_direct_sqlite_connect_outside_db():
    offenders = []
    for path in SRC_DIR.rglob("*.py"):
        rel = path.relative_to(SRC_DIR).as_posix()
        if rel in _PRE_EXISTING_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        if _CONNECT_RE.search(text):
            offenders.append(rel)
    assert not offenders, (
        f"прямой sqlite3.connect(...) вне db.get_conn в: {offenders} — "
        "используй krisha.db.get_conn/use_conn (WAL + busy_timeout + synchronous)"
    )
