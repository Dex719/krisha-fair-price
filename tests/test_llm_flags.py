"""Этап 5: кэш llm_flags, парсинг ответа Gemini и сборка бейджей."""

import json

import pytest

from krisha import llm_flags
from krisha.llm_flags import (
    FLAGS_RU,
    _parse_response,
    build_text_flags,
    get_cached_flags,
    save_flags,
)


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(llm_flags, "DB_PATH", db)
    return db


def _gemini_payload(rows) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(rows)}], "role": "model"}}
        ]
    }


def test_parse_response_valid():
    payload = _gemini_payload(
        [
            {"id": 1, "flags": ["urgent_sale", "bargain"]},
            {"id": 2, "flags": []},
        ]
    )
    out = _parse_response(payload)
    assert out == {1: ["urgent_sale", "bargain"], 2: []}


def test_parse_response_filters_unknown_flags_and_bad_rows():
    payload = _gemini_payload(
        [
            {"id": 1, "flags": ["urgent_sale", "made_up_flag"]},
            {"flags": ["bargain"]},          # нет id — пропускаем
            {"id": "oops", "flags": []},      # кривой id — пропускаем
        ]
    )
    assert _parse_response(payload) == {1: ["urgent_sale"]}


def test_parse_response_garbage():
    assert _parse_response({}) == {}
    assert _parse_response(_gemini_payload("not a list")) == {}


def test_cache_roundtrip(tmp_db):
    text = "Срочно продам, торг уместен!"
    assert get_cached_flags(101, text) is None
    save_flags(101, text, ["urgent_sale", "bargain"])
    assert get_cached_flags(101, text) == ["urgent_sale", "bargain"]
    # описание поменялось → кэш считается устаревшим
    assert get_cached_flags(101, text + " Новая цена.") is None
    # повторное сохранение перезаписывает
    save_flags(101, text, ["bargain"])
    assert get_cached_flags(101, text) == ["bargain"]


def test_build_text_flags_from_cache(tmp_db):
    text = "Тихий двор, окна во двор. Квартира тёплая."
    save_flags(7, text, ["quiet_area", "windows_courtyard"])
    badges = build_text_flags({"id": 7, "description": text}, live=False)
    assert badges == [
        {"kind": "plus", "label": FLAGS_RU["quiet_area"][1]},
        {"kind": "plus", "label": FLAGS_RU["windows_courtyard"][1]},
    ]


def test_build_text_flags_no_key_no_cache(tmp_db, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert build_text_flags({"id": 8, "description": "Продам квартиру срочно"}) == []


def test_build_text_flags_no_description(tmp_db):
    assert build_text_flags({"id": 9, "description": None}) == []
    assert build_text_flags({"id": None, "description": "Текст"}) == []


def test_build_text_flags_live_saves_cache(tmp_db, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm_flags, "analyze_one", lambda lid, text, api_key=None: ["pledge"])
    text = "Квартира в залоге у банка."
    badges = build_text_flags({"id": 10, "description": text})
    assert badges == [{"kind": "warn", "label": FLAGS_RU["pledge"][1]}]
    # второй вызов берёт из кэша (analyze_one больше не нужен)
    monkeypatch.setattr(llm_flags, "analyze_one", lambda *a, **k: pytest.fail("должен быть кэш"))
    assert build_text_flags({"id": 10, "description": text}) == badges
