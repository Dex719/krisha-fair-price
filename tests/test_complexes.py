"""Тесты этапа 2: нормализация имён ЖК, парсер страницы ЖК, фичи из справочника."""

import pandas as pd

from krisha.complexes import normalize_complex_name
from krisha.features import add_complex_features
from krisha.scraping.complex_parser import parse_completion_year, parse_max_floors

LOOKUP = {
    "хан тенгри": {
        "developer": "BI Group", "housing_class": "комфорт",
        "completion_year": 2020, "apartments_count": 300,
    },
}


def test_normalize_complex_name():
    assert normalize_complex_name('ЖК "Хан-Тенгри"') == "хан тенгри"
    assert normalize_complex_name("Хан  Тенгри") == "хан тенгри"
    assert normalize_complex_name("Коттеджный городок Tauda Villa 3.0") == "tauda villa 3 0"
    assert normalize_complex_name(None) == ""
    assert normalize_complex_name("  ") == ""


def test_parse_completion_year_takes_last_phase():
    text = "Первая очередь - III квартал 2025 г. Вторая очередь - IV квартал 2026 г."
    assert parse_completion_year(text) == 2026
    assert parse_completion_year("сдан") is None
    assert parse_completion_year(None) is None


def test_parse_max_floors():
    assert parse_max_floors("7-9 этажей") == 9
    assert parse_max_floors("12 этажей") == 12
    assert parse_max_floors(None) is None
    assert parse_max_floors("высотный") is None


def test_add_complex_features_joins_by_raw_params():
    df = pd.DataFrame([
        {"raw_params": '{"map.complex": "Хан Тенгри"}'},
        {"raw_params": "{}", "complex_name": 'ЖК "Хан-Тенгри"'},
        {"raw_params": "{}"},
    ])
    out = add_complex_features(df, lookup=LOOKUP)
    assert out.loc[0, "developer"] == "BI Group"
    assert out.loc[0, "completion_year"] == 2020
    assert out.loc[1, "housing_class"] == "комфорт"  # фолбэк на complex_name
    assert pd.isna(out.loc[2, "developer"])  # неизвестный ЖК — пропуск


def test_lookup_complex_attrs_strips_phase_number():
    from krisha.complexes import lookup_complex_attrs

    assert lookup_complex_attrs("Хан Тенгри 2", LOOKUP)["developer"] == "BI Group"
    assert lookup_complex_attrs("Хан Тенгри 2.0", LOOKUP)["completion_year"] == 2020
    assert lookup_complex_attrs("Неизвестный ЖК", LOOKUP) == {}
