"""extract_window_json: бракет-корректный парсинг window.data (issue #100)."""

from krisha.scraping.json_window import extract_window_json


def test_extracts_simple_object():
    html = '<script>window.data = {"id": 1, "price": 100};</script>'
    assert extract_window_json(html) == {"id": 1, "price": 100}


def test_survives_nested_object_with_its_own_closing_brace_semicolon():
    """Раньше нежадный regex `(\\{.*?\\});` обрывался на ПЕРВОМ `};` — если
    вложенный подобъект сам заканчивался на `};`-подобную последовательность
    внутри строки, JSON обрубался и json.loads падал."""
    html = (
        "<script>window.data = "
        '{"advert": {"id": 1}, "note": "штрихкод: 12};34", "price": 100}'
        ";</script>"
    )
    data = extract_window_json(html)
    assert data == {"advert": {"id": 1}, "note": "штрихкод: 12};34", "price": 100}


def test_survives_string_value_containing_brace_semicolon_literally():
    html = (
        '<script>window.data = {"description": "цена от 40 000 000; акция};", '
        '"price": 5};</script>'
    )
    data = extract_window_json(html)
    assert data == {"description": "цена от 40 000 000; акция};", "price": 5}


def test_missing_window_data_returns_none():
    assert extract_window_json("<html><body>nothing here</body></html>") is None


def test_malformed_json_returns_none():
    assert extract_window_json("window.data = {not valid json};") is None


def test_custom_var_name_for_complex_pages():
    html = 'window.complexData = {"complex": {"id": 5}};'
    assert extract_window_json(html, "complexData") == {"complex": {"id": 5}}
