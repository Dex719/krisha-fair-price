"""Тесты подсказок к факторам цены (этаж) и кэша KD-дерева."""

import json

from krisha.factor_hints import _floor_hint
from krisha.spatial import _TREE_KEY, _ref_tree, save_spatial_ref

_STATS = {"last_floor": 900_000.0, "mid_floor": 1_000_000.0, "first_floor": 930_000.0}


def test_last_floor_highrise_mentions_lift_and_seismic():
    hint = _floor_hint({"floor": 9, "total_floors": 9}, _STATS)
    assert "Этаж 9 из 9" in hint
    assert "последний" in hint
    assert "лифт" in hint
    assert "сейсмо" in hint
    assert "на 10% дешевле" in hint


def test_last_floor_lowrise_no_lift_note():
    hint = _floor_hint({"floor": 5, "total_floors": 5}, _STATS)
    assert "последний" in hint
    assert "лифт" not in hint
    assert "сейсмо" not in hint


def test_high_floor_not_last():
    hint = _floor_hint({"floor": 12, "total_floors": 16}, _STATS)
    assert "Этаж 12 из 16" in hint
    assert "лифт" in hint


def test_mid_floor_stays_positive():
    hint = _floor_hint({"floor": 3, "total_floors": 9}, _STATS)
    assert "самые ликвидные" in hint


def test_ref_tree_cached_and_not_serialized(tmp_path):
    ref = {
        "lat": [43.24, 43.25, 43.26],
        "lon": [76.9, 76.91, 76.92],
        "ppsm": [1e6, 1.1e6, 1.2e6],
        "hex7": {},
        "hex8": {},
    }
    tree1 = _ref_tree(ref)
    tree2 = _ref_tree(ref)
    assert tree1 is tree2  # второе обращение — из кэша
    assert _TREE_KEY in ref

    path = tmp_path / "spatial_ref.json"
    save_spatial_ref(ref, path)  # дерево не должно попасть в json и не должно упасть
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert _TREE_KEY not in saved
    assert saved["lat"] == ref["lat"]


def test_market_stats_without_db_does_not_create_an_empty_db(tmp_path, monkeypatch):
    """Нет базы — пустая статистика и НИКАКОГО файла на её месте.

    sqlite3.connect на отсутствующий путь создаёт пустой файл: после первого же
    предикта карточки DB_PATH.exists() начинал врать, и в CI «нет базы → 503» в
    /api/demo превращалось в «no such table: listings» (PR #194)."""
    from krisha import factor_hints

    missing = tmp_path / "data" / "krisha.db"
    monkeypatch.setattr(factor_hints, "DB_PATH", missing)
    factor_hints._market_stats_cached.cache_clear()

    assert factor_hints.market_stats() == {}
    assert not missing.exists()
