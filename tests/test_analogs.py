"""Тесты kNN-аналогов: отбор кандидатов и ранжирование."""

from krisha.analogs import find_analogs
from krisha.db import init_db, upsert_listing

BASE = {
    "url": None,
    "title": None,
    "price": 50_000_000,
    "area": 60.0,
    "rooms": 2,
    "year_built": 2015,
    "district": "Bostandykskiy_r-n",
    "lat": 43.20,
    "lon": 76.90,
    "source": "test",
}


def _fill_db(db):
    init_db(db)
    rows = [
        # тот же район, близко, площадь почти та же — лучший аналог
        {"id": 1, "area": 62.0, "lat": 43.201, "lon": 76.901},
        # тот же район, но дальше и старше
        {"id": 2, "area": 58.0, "lat": 43.25, "lon": 76.95, "year_built": 1995},
        # другой район — идёт после однорайонных
        {"id": 3, "area": 60.0, "district": "Almalinskiy_r-n"},
        # другая комнатность — не кандидат
        {"id": 4, "rooms": 3},
        # площадь вне ±25% — не кандидат
        {"id": 5, "area": 100.0},
    ]
    for r in rows:
        upsert_listing({**BASE, "url": f"https://krisha.kz/a/show/{r['id']}", **r}, db_path=db)


def test_find_analogs_ranking(tmp_path):
    db = tmp_path / "krisha.db"
    _fill_db(db)
    subject = {**BASE, "id": 999}
    analogs = find_analogs(subject, db_path=db)
    ids = [a["id"] for a in analogs]
    assert ids[0] == 1  # ближайший в том же районе
    assert set(ids) == {1, 2, 3}  # 4 и 5 отсеяны фильтрами
    assert ids.index(2) < ids.index(3)  # свой район раньше чужого
    assert analogs[0]["ppsm"] == round(50_000_000 / 62.0)


def test_find_analogs_excludes_subject_and_missing_data(tmp_path):
    db = tmp_path / "krisha.db"
    _fill_db(db)
    # сам лот не попадает в свои аналоги
    ids = [a["id"] for a in find_analogs({**BASE, "id": 1}, db_path=db)]
    assert 1 not in ids
    # без комнат/площади — пусто, без падений
    assert find_analogs({"id": 7, "rooms": None, "area": None}, db_path=db) == []
    # базы нет — пусто
    assert find_analogs({**BASE, "id": 9}, db_path=tmp_path / "none.db") == []
