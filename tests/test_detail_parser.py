from pathlib import Path

import pytest

from krisha.scraping.detail_parser import parse_detail
from krisha.scraping.listing_parser import has_next_page, parse_listing_ids

FIXTURE = (Path(__file__).parent / "fixtures" / "detail_sample.html").read_text(
    encoding="utf-8"
)


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
    html = '<a href="/a/show/111">x</a> <a href="/a/show/222">y</a> <a href="/a/show/111">dup</a>'
    assert parse_listing_ids(html) == [111, 222]


def test_has_next_page_structural_paginator():
    """issue #99: пагинатор определяется структурно, не подстрокой page=N+1."""
    with_next = '<div class="paginator"><a class="paginator__btn--next" href="?page=2">→</a></div>'
    assert has_next_page(with_next, 1)

    last_page = (
        '<div class="paginator">'
        '<span class="paginator__btn--next paginator__btn--disabled">→</span>'
        "</div>"
    )
    assert not has_next_page(last_page, 5)

    assert has_next_page('<link rel="next" href="?page=2">', 1)
    assert not has_next_page("<html><body>no paginator here</body></html>", 1)


def test_has_next_page_ignores_page_substring_outside_paginator():
    """issue #99 регрессия: JS-стейт/canonical/аналитика содержат "page=2" без
    реального пагинатора — раньше это ложно продлевало обход до max_pages."""
    html = (
        '<link rel="canonical" href="https://krisha.kz/prodazha/kvartiry/almaty/?page=2">'
        '<script>window.dataLayer.push({"label": "page=2"});</script>'
        '<div class="paginator"></div>'
    )
    assert not has_next_page(html, 1)
