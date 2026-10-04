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


def test_market_stats_cache_survives_writes_to_the_db(tmp_path, monkeypatch):
    """Предикт сам пишет в базу (upsert + лог), и mtime файла меняется.
    Кэш по mtime сбрасывался каждым запросом: +1.9 с на каждую проверку."""
    from krisha import factor_hints
    from krisha.db import init_db, upsert_listing

    db = tmp_path / "k.db"
    init_db(db)
    monkeypatch.setattr(factor_hints, "DB_PATH", db)
    factor_hints._market_stats_cached.cache_clear()
    factor_hints.market_stats()
    upsert_listing({"id": 1, "url": "u", "price": 30_000_000, "area": 50.0, "source": "user"}, db_path=db)
    factor_hints.market_stats()

    info = factor_hints._market_stats_cached.cache_info()
    assert info.misses == 1 and info.hits == 1


# --- подсказки не противоречат объявлению ---------------------------------------

_MARKET = {"city": 1_000_000.0, "n_city": 100, "district": {}}


def _hints(monkeypatch, listing, features, facts=None):
    from krisha import factor_hints

    monkeypatch.setattr(factor_hints, "market_stats", lambda: _MARKET)
    factors = [{"feature": f, "impact": -0.05} for f in features]
    return {f["feature"]: f["hint"] for f in factor_hints.build_factor_hints(listing, factors, facts)}


def test_housing_class_hint_for_old_house_does_not_talk_about_new_buildings(monkeypatch):
    """Под домом 1970 года было «Класс жилья … самый сильный фактор в новостройках»."""
    hints = _hints(
        monkeypatch, {"year_built": 1970, "total_floors": 5},
        ["housing_class", "developer", "complex_name"],
        facts={"housing_class": "unknown", "developer": "unknown", "complex_name": "unknown"},
    )

    assert "не указан" in hints["housing_class"]
    assert "новостройках" not in hints["housing_class"]
    assert "не указан" in hints["developer"]
    assert "не относится к жилому комплексу" in hints["complex_name"]


def test_housing_class_hint_names_the_known_class(monkeypatch):
    hints = _hints(monkeypatch, {"year_built": 2022}, ["housing_class"], facts={"housing_class": "бизнес"})

    assert "«бизнес»" in hints["housing_class"]


def test_total_floors_hint_follows_age_of_the_house(monkeypatch):
    old_tower = _hints(monkeypatch, {"year_built": 1978, "total_floors": 12}, ["total_floors"],
                       facts={"is_new_building": 0})["total_floors"]
    new_tower = _hints(monkeypatch, {"year_built": 2023, "total_floors": 16}, ["total_floors"],
                       facts={"is_new_building": 1})["total_floors"]
    old_low = _hints(monkeypatch, {"year_built": 1965, "total_floors": 4}, ["total_floors"],
                     facts={"is_new_building": 0})["total_floors"]
    new_low = _hints(monkeypatch, {"year_built": 2024, "total_floors": 4}, ["total_floors"],
                     facts={"is_new_building": 1})["total_floors"]

    assert "новые ЖК" not in old_tower and "1978" in old_tower
    assert "новые ЖК" in new_tower
    assert "старый фонд" in old_low
    assert "старый фонд" not in new_low


def test_merged_factor_keys_get_their_hints(monkeypatch):
    """После слияния (predict.FACTOR_GROUPS) приходят building_age, floor,
    district, lat — у каждого своя подсказка, а не пустота."""
    hints = _hints(
        monkeypatch,
        {"year_built": 1970, "floor": 3, "total_floors": 5, "lat": 43.24, "lon": 76.9},
        ["building_age", "floor", "district", "lat", "is_new_building", "ceiling"],
        facts={"is_new_building": 0},
    )

    assert "Дом 1970 года" in hints["building_age"] and "старый фонд" in hints["building_age"]
    assert "Этаж 3 из 5" in hints["floor"]
    assert hints["district"]  # района в объявлении нет — общий текст, а не None
    assert "Координаты дома" in hints["lat"]
    assert hints["is_new_building"].startswith("Вторичка")
    assert hints["ceiling"] is None  # потолков нет — подсказки нет


def test_age_hint_uses_russian_plurals():
    from krisha.factor_hints import _years

    assert [_years(n) for n in (1, 2, 5, 11, 21, 22, 56)] == [
        "1 год", "2 года", "5 лет", "11 лет", "21 год", "22 года", "56 лет",
    ]


def test_age_hint_explains_when_segment_median_contradicts_the_text(monkeypatch):
    """«Старый фонд дешевле», а медиана старых домов выше городской (они в
    центре) — подсказка объясняет расхождение, а не спорит сама с собой."""
    from krisha import factor_hints

    market = {**_MARKET, "age_old": 1_050_000.0}
    hint = factor_hints._age_hint({"year_built": 1979}, market)

    assert "при прочих равных" in hint
    assert "ближе к центру" in hint
