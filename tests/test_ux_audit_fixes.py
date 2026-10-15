"""Правки по UX-аудиту 2026-10-15: бэкенд и тексты бота."""

from krisha.db import get_conn, init_db, upsert_listing
from krisha.numfmt import dec, pct, signed
from krisha.predict import _delisted_at
from krisha.rent import is_daily_rent
from krisha.scraping.detail_parser import clean_description

M = "−"


def test_numbers_in_bot_match_the_site():
    assert dec(43.84) == "43,8" and dec(-3.21) == f"{M}3,2" and dec(-0.01) == "0,0"
    assert signed(8.1) == "+8,1" and signed(-7.7) == f"{M}7,7" and signed(0) == "0,0"
    assert pct(-14.94) == f"{M}14,9%" and signed(14_000 / 1000, 0) == "+14"


def test_daily_rent_is_recognised_only_by_clear_signs():
    assert is_daily_rent({"title": "1-комнатная квартира посуточно · 40 м²", "price": 15_000})
    assert is_daily_rent({"title": "1-комнатная квартира · 40 м²", "price": 18_000,
                          "description": "Сдаю посуточно, чисто и уютно"})
    # помесячная с «посуточно не сдаю» в описании — не посуточная
    assert not is_daily_rent({"title": "1-комнатная квартира · 40 м²", "price": 250_000,
                              "description": "Посуточно не сдаю, только на длительный срок"})
    # «круглосуточная охрана» — не про сутки аренды
    assert not is_daily_rent({"title": "2-комнатная квартира · 60 м²", "price": 300_000,
                              "raw_params": '{"flat.security": "круглосуточная охрана"}'})


def test_translate_widget_is_stripped_from_description():
    junk = "Код \"918\" Перевести Перевод может быть неточным Показать оригинал"
    assert clean_description(junk) == 'Код "918"'
    assert clean_description("Перевести Перевод может быть неточным Показать оригинал") is None
    assert clean_description("Обычный текст") == "Обычный текст"


def test_user_check_does_not_revive_delisted_listing(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    listing = {"id": 7, "url": "https://krisha.kz/a/show/7", "price": 40_000_000}
    upsert_listing(listing, db)
    with get_conn(db) as conn:
        conn.execute("UPDATE listings SET is_active = 0, delisted_at = '2026-10-01 08:00:00' WHERE id = 7")
    upsert_listing({**listing, "source": "user"}, db)
    with get_conn(db) as conn:
        row = conn.execute("SELECT is_active, delisted_at FROM listings WHERE id = 7").fetchone()
        assert row["is_active"] == 0 and row["delisted_at"] == "2026-10-01 08:00:00"
        # карточка честно говорит, что объявление снято
        assert _delisted_at(conn, 7) == "2026-10-01 08:00:00"
        assert _delisted_at(conn, 8) is None
