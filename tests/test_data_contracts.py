"""issue #103: data-contract проверка на входе upsert_listing — карантин
аномалий (цена/площадь/координаты вне разумного диапазона) вместо молчаливой
записи мусора в listings, плюс coords_approx (общий пин ЖК)."""

from krisha.config import ALMATY_BBOX, AREA_MIN, PRICE_MAX, PRICE_MIN, SHARED_PIN_MIN
from krisha.db import (
    count_parse_anomalies,
    find_duplicate_id,
    get_conn,
    init_db,
    upsert_listing,
)


def _listing(lid, **overrides):
    row = {
        "id": lid,
        "url": f"https://krisha.kz/a/show/{lid}",
        "price": 40_000_000,
        "title": "test",
        "rooms": 2,
        "area": 60.0,
        "district": "Бостандыкский",
        "floor": 5,
        "total_floors": 9,
        "lat": 43.22,
        "lon": 76.85,
    }
    row.update(overrides)
    return row


# ---------- цена ----------


def test_out_of_range_price_quarantined_and_old_value_kept(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(1, price=40_000_000), db)

    upsert_listing(_listing(1, price=PRICE_MIN - 1), db)  # garbage-парс

    with get_conn(db) as conn:
        price = conn.execute("SELECT price FROM listings WHERE id = 1").fetchone()[0]
    assert price == 40_000_000  # старая валидная цена не затёрта мусором
    assert count_parse_anomalies(db_path=db) == 1


def test_out_of_range_price_on_new_listing_left_null(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(2, price=PRICE_MAX + 1), db)  # первый раз видим лот

    with get_conn(db) as conn:
        row = conn.execute("SELECT price, title FROM listings WHERE id = 2").fetchone()
    assert row[0] is None  # нет старого значения, откатывать не на что
    assert row[1] == "test"  # остальные поля лота всё равно записаны
    assert count_parse_anomalies(db_path=db) == 1


def test_in_range_price_not_quarantined(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(3, price=PRICE_MIN), db)
    upsert_listing(_listing(3, price=PRICE_MAX), db)
    assert count_parse_anomalies(db_path=db) == 0


# ---------- площадь ----------


def test_out_of_range_area_dropped_not_overwriting_old(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_listing(_listing(4, area=60.0), db)

    upsert_listing(_listing(4, area=AREA_MIN - 1), db)  # битый парс площади

    with get_conn(db) as conn:
        area = conn.execute("SELECT area FROM listings WHERE id = 4").fetchone()[0]
    assert area == 60.0  # COALESCE — старое хорошее значение осталось
    assert count_parse_anomalies(db_path=db) == 1


# ---------- координаты ----------


def test_out_of_bbox_coords_quarantined_but_row_still_stored(tmp_path):
    """Координаты вне ALMATY_BBOX (issue #103) — попадают в parse_anomalies,
    но lat/lon в listings НЕ зануляются: train-time фильтр
    (train._filter_stale_and_out_of_area) сам их исключает через тот же
    bbox и специально не трогает лоты без координат вовсе — занулить здесь
    значило бы вернуть garbage в train под видом "координат нет"."""
    db = tmp_path / "test.db"
    init_db(db)
    astana = {"lat": 51.16, "lon": 71.47}  # другой город
    upsert_listing(_listing(5, **astana), db)

    with get_conn(db) as conn:
        row = conn.execute("SELECT lat, lon FROM listings WHERE id = 5").fetchone()
    assert row[0] == astana["lat"]
    assert row[1] == astana["lon"]
    assert count_parse_anomalies(db_path=db) == 1


def test_out_of_bbox_coords_excluded_from_fingerprint_dedup(tmp_path):
    """Две разные квартиры, случайно получившие один и тот же битый (вне
    bbox) парс координат, не должны склеиваться дедупом по fingerprint."""
    db = tmp_path / "test.db"
    init_db(db)
    garbage = {"lat": 0.0, "lon": 0.0}
    upsert_listing(_listing(6, **garbage), db)
    upsert_listing(_listing(7, **garbage), db)

    with get_conn(db) as conn:
        fp6 = conn.execute("SELECT fingerprint FROM listings WHERE id = 6").fetchone()[0]
        fp7 = conn.execute("SELECT fingerprint FROM listings WHERE id = 7").fetchone()[0]
    assert fp6 is None and fp7 is None
    assert find_duplicate_id(fp6, exclude_id=6, db_path=db) is None


def test_in_bbox_coords_produce_normal_fingerprint_dedup(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    same_spot = {"lat": 43.22, "lon": 76.85, "district": "Бостандыкский", "area": 60.0,
                 "floor": 5, "total_floors": 9}
    upsert_listing(_listing(8, **same_spot), db)
    upsert_listing(_listing(9, **same_spot), db)

    with get_conn(db) as conn:
        fp8 = conn.execute("SELECT fingerprint FROM listings WHERE id = 8").fetchone()[0]
    assert fp8 is not None
    assert find_duplicate_id(fp8, exclude_id=9, db_path=db) == 8


# ---------- coords_approx (общий пин ЖК) ----------


def test_coords_approx_flags_shared_pin_once_threshold_reached(tmp_path):
    """coords_approx считается лениво, при upsert самой записи — не ретроактивно
    для уже записанных соседей по точке (та же модель, что live shared_pin в
    zones.py: пересчитывается по мере прихода новых upsert'ов, не разовым
    полным проходом). Достигнув порога, САМ upsert'нутый лот получает флаг 1."""
    db = tmp_path / "test.db"
    init_db(db)
    spot = {"lat": 43.20, "lon": 76.90}
    for lid in range(1, SHARED_PIN_MIN):  # ещё не достигли порога
        upsert_listing(_listing(lid, **spot), db)
        with get_conn(db) as conn:
            flag = conn.execute(
                "SELECT coords_approx FROM listings WHERE id = ?", (lid,)
            ).fetchone()[0]
        assert flag == 0

    upsert_listing(_listing(SHARED_PIN_MIN, **spot), db)  # порог достигнут (считая себя)
    with get_conn(db) as conn:
        flag = conn.execute(
            "SELECT coords_approx FROM listings WHERE id = ?", (SHARED_PIN_MIN,)
        ).fetchone()[0]
    assert flag == 1


def test_coords_approx_preserved_when_later_upsert_has_no_coords(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    spot = {"lat": 43.20, "lon": 76.90}
    for lid in range(1, SHARED_PIN_MIN + 1):
        upsert_listing(_listing(lid, **spot), db)
    # lid=1 сам был upsert'нут до того, как остальные 4 появились в базе —
    # его coords_approx посчитан лениво по состоянию на тот момент (0).
    # Реальный пересчёт происходит на следующий upsert этой же записи.
    upsert_listing(_listing(1, **spot), db)
    with get_conn(db) as conn:
        flag = conn.execute("SELECT coords_approx FROM listings WHERE id = 1").fetchone()[0]
    assert flag == 1

    # Повторный upsert без координат (например только цена изменилась) —
    # не должен молча сбросить уже посчитанный coords_approx в 0/NULL.
    upsert_listing({"id": 1, "url": "https://krisha.kz/a/show/1", "price": 41_000_000}, db)
    with get_conn(db) as conn:
        flag = conn.execute("SELECT coords_approx FROM listings WHERE id = 1").fetchone()[0]
    assert flag == 1


def test_almaty_bbox_sanity():
    # координаты центра Алматы должны укладываться в текущий bbox
    assert ALMATY_BBOX["lat_min"] < 43.24 < ALMATY_BBOX["lat_max"]
    assert ALMATY_BBOX["lon_min"] < 76.89 < ALMATY_BBOX["lon_max"]


def test_rent_prices_survive_the_price_contract(tmp_path):
    """Регрессия: контракт цены был захардкожен на продажу (PRICE_MIN=5 млн),
    поэтому КАЖДАЯ арендная цена (₸/месяц) считалась мусором. Ни одна цена в
    krisha_rent.db не обновлялась, price_history аренды стояла пустой."""
    from krisha.db import RENT_PRICE_BOUNDS, SALE_PRICE_BOUNDS, is_valid_price, price_bounds_for

    assert price_bounds_for("arenda") == RENT_PRICE_BOUNDS
    assert price_bounds_for("prodazha") == SALE_PRICE_BOUNDS
    assert price_bounds_for(None) == SALE_PRICE_BOUNDS

    rent = price_bounds_for("arenda")
    assert is_valid_price(350_000, rent), "типичная аренда 350к ₸/мес должна проходить"
    assert not is_valid_price(350_000), "по продажному контракту она же — мусор"
    assert not is_valid_price(5, rent)


def test_upsert_does_not_null_out_a_stored_price(tmp_path):
    """Регрессия: price лежит в _UPSERT_ALWAYS (пишется безусловно), а
    is_valid_price(None) → True, поэтому парс без цены («договорная», битая
    страница) затирал хорошую цену NULL-ом. И залипало: на следующем проходе
    _record_price_if_changed видел в истории ту же цену и не чинил поле."""
    from krisha.db import get_conn, init_db, upsert_listing

    db = tmp_path / "t.db"
    init_db(db)
    base = {"id": 7, "url": "u", "price": 30_000_000, "area": 60.0,
            "lat": 43.24, "lon": 76.89, "title": "Квартира"}
    upsert_listing(base, db)
    upsert_listing({**base, "price": None}, db)

    with get_conn(db) as conn:
        price = conn.execute("SELECT price FROM listings WHERE id = 7").fetchone()[0]
    assert price == 30_000_000, "отсутствие цены в свежем парсе не должно затирать базу"


def test_sighting_quarantines_out_of_contract_card_price(tmp_path):
    """Регрессия: record_sighting (путь НОВОГО id) писал цену карточки вообще
    без проверки — та же величина для знакомого id отбраковывалась. Строку
    sighting пишем всё равно (нужны first_seen и место в очереди деталей),
    но без цены и с аномалией в parse_anomalies."""
    from krisha.db import count_parse_anomalies, get_conn, init_db, record_sighting

    db = tmp_path / "t.db"
    init_db(db)
    record_sighting(4242, "https://krisha.kz/a/show/4242", 1, db)

    with get_conn(db) as conn:
        row = conn.execute("SELECT price FROM listings WHERE id = 4242").fetchone()
        history = conn.execute(
            "SELECT COUNT(*) FROM price_history WHERE listing_id = 4242"
        ).fetchone()[0]
    assert row is not None, "сам sighting должен сохраниться (issue #127)"
    assert row[0] is None
    assert history == 0, "мусорная цена не должна попадать в историю"
    assert count_parse_anomalies(db_path=db) == 1
