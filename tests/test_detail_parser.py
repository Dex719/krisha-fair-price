from pathlib import Path

import pytest

from krisha.scraping.detail_parser import parse_detail
from krisha.scraping.listing_parser import has_next_page, parse_listing_ids

FIXTURE = (Path(__file__).parent / "fixtures" / "detail_sample.html").read_text()


@pytest.fixture()
def listing():
    return parse_detail(FIXTURE, "https://krisha.kz/a/show/123456789")


def test_basic_fields(listing):
    assert listing["id"] == 123456789
    assert listing["price"] == 42_000_000
    assert listing["rooms"] == 2
    assert listing["area"] == 60.0
    assert listing["category"] == "vtorichka"
    assert listing["user_type"] == "owner"


def test_floor_parsed_from_params(listing):
    assert listing["floor"] == 4
    assert listing["total_floors"] == 9


def test_html_params(listing):
    assert listing["building_type"] == "кирпичный"
    assert listing["year_built"] == 2012
    assert listing["ceiling"] == 2.8


def test_address_and_coords(listing):
    assert listing["district"] == "Bostandykskiy_r-n"
    assert listing["street"] == "Timiryazeva"
    assert listing["lat"] == pytest.approx(43.2284)
    assert listing["photos_count"] == 2


def test_description(listing):
    assert "уютная квартира" in listing["description"]


def test_parse_detail_garbage_returns_none():
    assert parse_detail("<html><body>nothing here</body></html>") is None


def test_parse_listing_ids():
    html = (
        '<a href="/a/show/111">x</a> <a href="/a/show/222">y</a> '
        '<a href="/a/show/111">dup</a> <a href="?page=2">next</a>'
    )
    assert parse_listing_ids(html) == [111, 222]
    assert has_next_page(html, 1)
    assert not has_next_page(html, 5)
