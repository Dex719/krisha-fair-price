"""krisha не отдаёт страницу объявления — оцениваем по своей базе.

С 2026-11-02 SafeLine закрыл /a/show/ для всех наших IP (468-челлендж или
403-блок), а выдача открыта: «оценить по ссылке» отвечало 503 на любую ссылку
(docs/ai-graph/incidents/2026-11-02-prod-smoke-red.md). Теперь при отказе
источника predict_from_url берёт строку из базы своей сделки, если в ней есть
поля детальной страницы, и помечает ответ source="db" + data_as_of. Контракт
ответа /api/predict: source "live" | "db", data_as_of "YYYY-MM-DD" | null.

Базы — только во временной папке: в data/ репозитория тесты не пишут.
"""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from krisha import bot, db, predict_gate
from krisha import predict as predict_mod
from krisha.api import app as app_module
from krisha.api import metrics
from krisha.api.app import app
from krisha.api.schemas import PredictResponse
from krisha.scraping.client import ChallengeBlocked

FIXTURE = Path(__file__).parent / "fixtures" / "detail_sample.html"
ROOT = Path(__file__).resolve().parents[1]
LID = 1017074246
URL = f"https://krisha.kz/a/show/{LID}"
LAST_SEEN = "2026-11-01 20:50:29"

# Строка после докачки детальной страницы (всё, что нужно модели)
SALE_ROW = {
    "id": LID,
    "url": URL,
    "title": "2-комнатная квартира · 60 м² · 4/9 этаж",
    "price": 42_000_000,
    "rooms": 2,
    "area": 60.0,
    "floor": 4,
    "total_floors": 9,
    "building_type": "монолитный",
    "year_built": 2015,
    "district": "Bostandykskiy_r-n",
    "address_title": "Бостандыкский р-н, Тимирязева 42",
    "lat": 43.2284,
    "lon": 76.9123,
    "user_type": "owner",
    "category": "vtorichka",
    "description": "Светлая квартира",
    "photos_count": 2,
    "raw_params": "{}",
    "source": "scrape",
}
RENT_ROW = {
    **SALE_ROW,
    "title": "2-комнатная квартира · 60 м² · 4/9 этаж",
    "price": 300_000,
    "year_built": None,  # аренда: krisha почти никогда не показывает год постройки
}


@pytest.fixture(autouse=True)
def _tmp_dbs(tmp_path, monkeypatch):
    """Обе базы предикта — во временной папке (по умолчанию их нет)."""
    sale, rent = tmp_path / "krisha.db", tmp_path / "krisha_rent.db"
    monkeypatch.setattr(predict_mod, "DB_PATH", sale)
    monkeypatch.setattr(predict_mod, "RENT_DB_PATH", rent)
    return sale, rent


def _seed(db_path: Path, row: dict, last_seen: str = LAST_SEEN, bounds=None) -> None:
    db.init_db(db_path)
    db.upsert_listing(row, db_path=db_path, price_bounds=bounds)
    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE listings SET last_seen = ? WHERE id = ?", (last_seen, row["id"]))


def _dump(db_path: Path) -> list[str]:
    with db.get_conn(db_path) as conn:
        return list(conn.iterdump())


class _Client:
    """Подмена user_client: страница или отказ источника, без сети."""

    def __init__(self, page: str | None = None, exc: Exception | None = None):
        self.page, self.exc, self.calls = page, exc, 0

    def __call__(self, timeout=None, on_attempt=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.page


def _blocked(monkeypatch) -> _Client:
    client = _Client(exc=ChallengeBlocked("3 попыток без страницы, из них 3 — SafeLine"))
    monkeypatch.setattr(predict_mod, "user_client", client)
    return client


def _fake_card(monkeypatch) -> list[dict]:
    """predict_from_listing без модели: запоминает, с чем его позвали."""
    calls: list[dict] = []

    def fake(listing, live_vision=True, conn=None, deal=None, record=True):
        calls.append(
            {"listing": listing, "live_vision": live_vision, "deal": deal, "record": record}
        )
        return {
            "deal": deal or "prodazha",
            "price_period": "month" if deal == "arenda" else None,
            "listing_id": listing["id"],
            "url": listing["url"],
            "title": listing.get("title"),
            "address": listing.get("address_title"),
            "actual_price": listing.get("price"),
            "fair_price": 40_000_000.0,
            "verdict": "FAIR",
            "diff_pct": 5.0,
            "top_factors": [],
        }

    monkeypatch.setattr(predict_mod, "predict_from_listing", fake)
    return calls


# --------------------------------------------------------------------------
# /api/predict: источник закрыт
# --------------------------------------------------------------------------
def test_blocked_source_with_usable_row_answers_from_db(_tmp_dbs, monkeypatch):
    sale, _ = _tmp_dbs
    _seed(sale, SALE_ROW)
    _blocked(monkeypatch)
    calls = _fake_card(monkeypatch)

    resp = TestClient(app).post("/api/predict", json={"url": URL})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "db"
    assert body["data_as_of"] == "2026-11-01"  # last_seen строки, YYYY-MM-DD
    assert body["listing_id"] == LID and body["deal"] == "prodazha"
    # без живого vision, без записи лога предикта, по продажной модели
    assert len(calls) == 1
    assert {k: calls[0][k] for k in ("live_vision", "deal", "record")} == {
        "live_vision": False,
        "deal": "prodazha",
        "record": False,
    }
    assert metrics.snapshot()["counters"]["predict_db_fallback"] == 1


@pytest.mark.parametrize(
    "seed",
    [
        # только выдача: id/url/цена (сайтинг без детали)
        lambda path: db.record_sighting(LID, URL, 42_000_000, db_path=path),
        # продажа без года постройки — модель продажи без него не оцениваем
        lambda path: _seed(path, {**SALE_ROW, "year_built": None}),
        # без координат
        lambda path: _seed(path, {**SALE_ROW, "lat": None, "lon": None}),
    ],
    ids=["sighting-only", "sale-without-year", "without-coords"],
)
def test_blocked_source_with_row_lacking_detail_fields_is_503(_tmp_dbs, monkeypatch, seed):
    sale, _ = _tmp_dbs
    db.init_db(sale)
    seed(sale)
    _blocked(monkeypatch)
    calls = _fake_card(monkeypatch)

    resp = TestClient(app).post("/api/predict", json={"url": URL})

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "5"
    assert calls == []


def test_blocked_source_without_row_is_503_and_creates_no_db(_tmp_dbs, monkeypatch):
    sale, rent = _tmp_dbs
    _blocked(monkeypatch)
    _fake_card(monkeypatch)

    resp = TestClient(app).post("/api/predict", json={"url": URL})

    assert resp.status_code == 503
    assert "Источник временно не отдаёт" in resp.json()["detail"]
    assert metrics.snapshot()["counters"]["predict_challenge"] == 1
    # sqlite3.connect создаёт пустой файл на месте отсутствующей базы — не должно
    assert not sale.exists() and not rent.exists()


def test_blocked_source_rent_row_without_year_answers_from_rent_db(_tmp_dbs, monkeypatch):
    """У аренды год постройки не обязателен: krisha его почти не показывает."""
    _, rent = _tmp_dbs
    _seed(rent, RENT_ROW, last_seen="2026-10-30 07:00:00", bounds=db.price_bounds_for("arenda"))
    _blocked(monkeypatch)
    calls = _fake_card(monkeypatch)

    result = predict_mod.predict_from_url(URL, live_vision=True)

    assert result["source"] == "db" and result["data_as_of"] == "2026-10-30"
    assert calls[0]["deal"] == "arenda" and calls[0]["live_vision"] is False


def test_legacy_rent_row_in_sale_db_is_answered_from_rent_db(_tmp_dbs, monkeypatch):
    """/track когда-то писал аренду в базу продажи: такую строку оцениваем по базе аренды."""
    sale, rent = _tmp_dbs
    db.init_db(sale)
    with db.get_conn(sale) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, price, rooms, area, lat, lon, last_seen) "
            "VALUES (?, ?, 300000, 2, 60, 43.2284, 76.9123, ?)",
            (LID, URL, LAST_SEEN),
        )
    _seed(rent, RENT_ROW, last_seen="2026-10-31 07:00:00", bounds=db.price_bounds_for("arenda"))
    _blocked(monkeypatch)
    calls = _fake_card(monkeypatch)

    result = predict_mod.predict_from_url(URL)

    assert calls[0]["deal"] == "arenda"
    assert result["data_as_of"] == "2026-10-31"


def test_fallback_does_not_write_to_db(_tmp_dbs, monkeypatch):
    sale, _ = _tmp_dbs
    _seed(sale, SALE_ROW)
    before = _dump(sale)
    _blocked(monkeypatch)
    _fake_card(monkeypatch)

    result = predict_mod.predict_from_url(URL)

    assert result["source"] == "db"
    assert _dump(sale) == before


@pytest.mark.needs_model
def test_fallback_with_real_model_writes_nothing(_tmp_dbs, monkeypatch):
    """Настоящая карточка по строке базы: оценка есть, база и лог предиктов не тронуты."""
    from krisha import factor_hints, prediction_log

    sale, _ = _tmp_dbs
    _seed(sale, SALE_ROW)
    monkeypatch.setattr(factor_hints, "DB_PATH", sale)
    factor_hints._market_stats_cached.cache_clear()
    logged: list[tuple] = []
    monkeypatch.setattr(prediction_log, "record", lambda *a, **k: logged.append(a))
    before = _dump(sale)
    _blocked(monkeypatch)

    result = predict_mod.predict_from_url(URL)

    assert result["source"] == "db" and result["data_as_of"] == "2026-11-01"
    assert result["fair_price"] > 0 and result["listing_id"] == LID
    PredictResponse(**result)  # ответ проходит схему API
    assert _dump(sale) == before
    assert logged == []


def test_live_path_is_marked_live(_tmp_dbs, monkeypatch):
    sale, _ = _tmp_dbs
    db.init_db(sale)
    monkeypatch.setattr(
        predict_mod, "user_client", _Client(page=FIXTURE.read_text(encoding="utf-8"))
    )
    calls = _fake_card(monkeypatch)

    resp = TestClient(app).post("/api/predict", json={"url": URL})

    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "live"
    assert resp.json()["data_as_of"] is None
    assert calls[0]["record"] is True


def test_response_schema_defaults_keep_old_payloads_valid():
    minimal = {
        "listing_id": 1,
        "url": "https://krisha.kz/a/show/1",
        "title": None,
        "address": None,
        "actual_price": None,
        "fair_price": 1.0,
        "verdict": None,
        "diff_pct": None,
        "top_factors": [],
    }
    old = PredictResponse(**minimal).model_dump()
    assert old["source"] == "live" and old["data_as_of"] is None
    assert PredictResponse(**minimal, source="db", data_as_of="2026-11-01").source == "db"


# --------------------------------------------------------------------------
# Калитка: оценка по базе живёт короче живой
# --------------------------------------------------------------------------
def _db_payload() -> dict:
    return {
        "listing_id": LID,
        "url": URL,
        "title": "t",
        "address": "a",
        "actual_price": 1,
        "fair_price": 1.0,
        "verdict": "FAIR",
        "diff_pct": 0.0,
        "top_factors": [],
        "source": "db",
        "data_as_of": "2026-11-01",
    }


def test_gate_keeps_db_result_for_short_ttl_only(monkeypatch):
    calls: list[int] = []

    def from_db(url, live_vision=False, timeout=None, on_attempt=None):
        calls.append(1)
        return _db_payload()

    monkeypatch.setattr(predict_gate, "predict_from_url", from_db)

    assert predict_gate.cached_predict(URL)["source"] == "db"
    assert predict_gate.cached_predict(URL)["source"] == "db"
    assert predict_gate.peek(URL)["source"] == "db"
    assert len(calls) == 1, "в пределах DB_RESULT_TTL_S повтор не ходит на krisha"
    assert predict_gate.DB_RESULT_TTL_S < predict_gate.PREDICT_CACHE_TTL_S
    assert len(predict_gate._cache) == 0, "оценка по базе не должна лечь в основной кэш"

    monkeypatch.setattr(predict_gate, "DB_RESULT_TTL_S", 0.0)  # минута прошла
    assert predict_gate.peek(URL) is None
    predict_gate.cached_predict(URL)
    assert len(calls) == 2


def test_gate_db_result_answers_every_waiter_with_one_scrape(monkeypatch):
    calls: list[int] = []

    def slow_db(url, live_vision=False, timeout=None, on_attempt=None):
        calls.append(1)
        time.sleep(0.2)
        return _db_payload()

    monkeypatch.setattr(predict_gate, "predict_from_url", slow_db)
    results: list[dict] = []
    threads = [
        threading.Thread(target=lambda: results.append(predict_gate.cached_predict(URL)))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert len(results) == 8 and all(r["source"] == "db" for r in results)


# --------------------------------------------------------------------------
# Демо-пул: только лоты, которые оцениваются и без страницы krisha
# --------------------------------------------------------------------------
def test_demo_pool_excludes_rows_without_detail_fields(tmp_path):
    sale = tmp_path / "demo.db"
    _seed(sale, {**SALE_ROW, "id": 1, "url": "https://krisha.kz/a/show/1"})
    _seed(sale, {**SALE_ROW, "id": 2, "url": "https://krisha.kz/a/show/2", "year_built": None})
    _seed(
        sale, {**SALE_ROW, "id": 3, "url": "https://krisha.kz/a/show/3", "lat": None, "lon": None}
    )
    db.record_sighting(4, "https://krisha.kz/a/show/4", 42_000_000, db_path=sale)

    assert app_module._demo_pool(sale) == [(1, "https://krisha.kz/a/show/1")]

    rent = tmp_path / "rent.db"
    bounds = db.price_bounds_for("arenda")
    _seed(rent, {**RENT_ROW, "id": 5, "url": "https://krisha.kz/a/show/5"}, bounds=bounds)
    _seed(
        rent,
        {**RENT_ROW, "id": 6, "url": "https://krisha.kz/a/show/6", "lat": None, "lon": None},
        bounds=bounds,
    )
    # у аренды год постройки не обязателен
    assert app_module._demo_pool(rent, deal="arenda") == [(5, "https://krisha.kz/a/show/5")]


def test_demo_pool_query_still_walks_the_last_seen_index(tmp_path):
    sale = tmp_path / "plan.db"
    _seed(sale, SALE_ROW)
    with db.get_conn(sale) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id, url FROM listings WHERE is_active = 1 "
            "AND url IS NOT NULL AND url LIKE '%krisha.kz/a/show/%' "
            f"AND {predict_mod.db_fallback_sql('prodazha')} ORDER BY last_seen DESC LIMIT 100"
        ).fetchall()
    text = " ".join(str(tuple(row)) for row in plan)
    assert "idx_listings_last_seen" in text, text
    assert "TEMP B-TREE" not in text, text


# --------------------------------------------------------------------------
# Что видит человек: строка в карточке бота и на сайте
# --------------------------------------------------------------------------
NOTE = "krisha.kz сейчас не отдаёт страницу объявления"


def test_bot_card_says_estimate_is_from_our_data():
    card = {
        "deal": "prodazha",
        "actual_price": 42_000_000,
        "fair_price": 40_000_000.0,
        "verdict": "FAIR",
        "diff_pct": 5.0,
        "top_factors": [],
    }
    text = bot.format_reply({**card, "source": "db", "data_as_of": "2026-11-01"})
    assert f"Оценка по нашим данным на 01.11.2026 — {NOTE}" in text

    assert NOTE not in bot.format_reply({**card, "source": "live", "data_as_of": None})
    assert NOTE not in bot.format_reply(card)  # старый ответ без поля
    assert bot.db_source_note(None) == f"Оценка по нашим данным — {NOTE}"


def test_site_card_renders_db_source_line():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "r.source === 'db'" in html
    assert "Оценка по нашим данным" in html and NOTE in html
    assert "r.data_as_of" in html
