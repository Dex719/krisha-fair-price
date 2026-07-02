"""Тесты zones.py: точка-в-полигоне, восстановление района/микрорайона, пины ЖК."""

import json

import pandas as pd
import pytest

from krisha.zones import (
    ZoneIndex,
    approximate_pin_note,
    load_zone_index,
    point_in_polygon,
    point_in_ring,
    resolve_zones,
)

# Квадрат ~центр Алматы: lat 43.20–43.30, lon 76.85–76.95
SQUARE = [[43.20, 76.85], [43.20, 76.95], [43.30, 76.95], [43.30, 76.85], [43.20, 76.85]]
# Квадрат восточнее: lon 76.95–77.05
SQUARE_EAST = [[43.20, 76.95], [43.20, 77.05], [43.30, 77.05], [43.30, 76.95], [43.20, 76.95]]
# Дырка внутри первого квадрата
HOLE = [[43.24, 76.89], [43.24, 76.91], [43.26, 76.91], [43.26, 76.89], [43.24, 76.89]]


@pytest.fixture()
def index() -> ZoneIndex:
    return ZoneIndex({
        "districts": [
            {"key": "Almalinskiy_r-n", "name_ru": "Алмалинский район",
             "polygon": {"outer": [SQUARE], "inner": []}},
            {"key": "Medeuskiy_r-n", "name_ru": "Медеуский район",
             "polygon": {"outer": [SQUARE_EAST], "inner": []}},
        ],
        "microdistricts": [
            {"label": "mkr_Test", "osm_name": "Тест",
             "polygon": {"outer": [HOLE], "inner": []}},
        ],
        "micro_centroids": {
            "mkr_Far": [43.29, 76.94, 10],   # у северо-восточного угла первого квадрата
            "mkr_Rare": [43.21, 76.86, 1],   # < MIN_N объявлений — игнорируется
        },
        "shared_pins": [[43.25, 76.9, 32]],
    })


def test_point_in_ring():
    assert point_in_ring(43.25, 76.90, SQUARE)
    assert not point_in_ring(43.25, 77.00, SQUARE)
    assert not point_in_ring(43.35, 76.90, SQUARE)


def test_point_in_polygon_with_hole():
    poly = {"outer": [SQUARE], "inner": [HOLE]}
    assert point_in_polygon(43.22, 76.87, poly)
    assert not point_in_polygon(43.25, 76.90, poly)  # в дырке


def test_district_lookup(index):
    assert index.district(43.25, 76.90) == "Almalinskiy_r-n"
    assert index.district(43.25, 77.00) == "Medeuskiy_r-n"
    assert index.district(43.40, 76.90) is None
    assert index.district(float("nan"), float("nan")) is None


def test_microdistrict_polygon_beats_centroid(index):
    # Точка в полигоне mkr_Test — полигон приоритетнее центроидов
    assert index.microdistrict(43.25, 76.90) == "mkr_Test"


def test_microdistrict_centroid_fallback(index):
    # Вне полигонов, в ~0.5 км от центроида mkr_Far
    assert index.microdistrict(43.293, 76.945) == "mkr_Far"
    # Слишком далеко от всех центроидов (> MICRO_CENTROID_MAX_KM)
    assert index.microdistrict(43.40, 77.10) is None


def test_centroid_with_few_listings_ignored(index):
    # mkr_Rare (1 объявление) не должен присваиваться даже рядом с его центром
    assert index.microdistrict(43.21, 76.86) != "mkr_Rare"


def test_resolve_zones_fills_and_flags(index):
    df = pd.DataFrame({
        "district": [None, "Almalinskiy_r-n", "Turksibskiy_r-n", "Almalinskiy_r-n"],
        "microdistrict": [None, "mkr_Kept", None, None],
        "lat": [43.25, 43.25, 43.25, None],
        "lon": [76.90, 76.90, 77.00, None],
    })
    out = resolve_zones(df, index=index)
    # Пропуск района заполнен по полигону
    assert out.loc[0, "district"] == "Almalinskiy_r-n"
    assert out.loc[0, "district_mismatch"] == 0
    # Совпадение — без изменений
    assert out.loc[1, "district"] == "Almalinskiy_r-n"
    assert out.loc[1, "district_mismatch"] == 0
    # Расхождение krisha vs OSM → доверяем координатам + флаг
    assert out.loc[2, "district"] == "Medeuskiy_r-n"
    assert out.loc[2, "district_mismatch"] == 1
    # Без координат ничего не трогаем
    assert out.loc[3, "district"] == "Almalinskiy_r-n"
    assert out.loc[3, "district_mismatch"] == 0
    # Микрорайон: пропуск заполнен, явная метка сохранена
    assert out.loc[0, "microdistrict"] == "mkr_Test"
    assert out.loc[1, "microdistrict"] == "mkr_Kept"


def test_resolve_zones_without_snapshot_is_noop():
    df = pd.DataFrame({"district": ["X"], "microdistrict": [None],
                       "lat": [43.25], "lon": [76.9]})
    out = resolve_zones(df, index=None) if load_zone_index() is None else None
    if out is not None:  # снапшота нет — полный no-op
        assert out.loc[0, "district"] == "X"
        assert out.loc[0, "district_mismatch"] == 0


def test_resolve_zones_idempotent(index):
    df = pd.DataFrame({"district": ["Turksibskiy_r-n"], "microdistrict": [None],
                       "lat": [43.25], "lon": [76.90]})
    once = resolve_zones(df, index=index)
    twice = resolve_zones(once, index=index)
    assert twice.loc[0, "district"] == "Almalinskiy_r-n"
    assert twice.loc[0, "district_mismatch"] == 0  # второй прогон уже без расхождения


def test_shared_pin_count(index):
    assert index.shared_pin_count(43.25, 76.9) == 32
    assert index.shared_pin_count(43.251, 76.9) == 0
    assert index.shared_pin_count(None, None) == 0


def test_approximate_pin_note(tmp_path, monkeypatch, index):
    # note строится через load_zone_index — подменяем снапшот на файл tmp
    snap = {
        "districts": [], "microdistricts": [], "micro_centroids": {},
        "shared_pins": [[43.25, 76.9, 32]],
    }
    path = tmp_path / "osm_zones.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    load_zone_index.cache_clear()
    monkeypatch.setattr("krisha.zones.OSM_ZONES_SNAPSHOT_PATH", path)
    try:
        note = approximate_pin_note(43.25, 76.9)
        assert note is not None and "32" in note["value"]
        assert approximate_pin_note(43.26, 76.9) is None
        assert approximate_pin_note(None, None) is None
    finally:
        load_zone_index.cache_clear()


def test_real_snapshot_if_present():
    """Смоук по реальному снапшоту: центр Алматы должен попасть в Алмалинский."""
    load_zone_index.cache_clear()
    index = load_zone_index()
    if index is None:
        pytest.skip("models/osm_zones.json отсутствует")
    assert len(index.districts) == 8
    assert index.district(43.2333, 76.9633) == "Medeuskiy_r-n"  # Кок-Тобе
    assert index.district(43.2398, 76.8898) == "Almalinskiy_r-n"  # Абая/Достык
    assert index.microdistrict(43.2296, 76.8360) == "mkr_Aksay-4"
