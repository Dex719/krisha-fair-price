"""Оцениваем только квартиры в Алматы: объявление из другого города не получает
вердикт и не оседает в базе — ни через сайт, ни через бота, ни через /track.

Обе модели учились только на Алматы; раньше ссылка на квартиру в Астане
получала «оценку» с потолка и ложилась в базу Алматы с source=user.
"""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from krisha import bot, predict_gate
from krisha.api.app import app
from krisha.predict import OUTSIDE_ALMATY, ListingOutsideAlmaty, is_almaty
from krisha.scraping.detail_parser import parse_detail

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "detail_sample.html"
URL = "https://krisha.kz/a/show/1013224169"


def test_parser_reads_city_from_krisha_address():
    listing = parse_detail(FIXTURE.read_text(encoding="utf-8"), URL)

    assert listing is not None and listing["city"] == "Almaty"


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ({"city": "Almaty"}, True),
        ({"city": "almaty "}, True),
        ({"city": "Astana", "lat": 43.24, "lon": 76.95}, False),  # город важнее координат
        ({"city": "Kaskelen"}, False),
        ({"city": None, "lat": 43.24, "lon": 76.95}, True),  # без города — по координатам
        ({"lat": 51.13, "lon": 71.43}, False),  # Астана
        ({"lat": "43.24", "lon": "76.95"}, True),
        ({}, True),  # проверить нечем — не отказываем
    ],
)
def test_is_almaty(listing, expected):
    assert is_almaty(listing) is expected


def test_predict_from_url_refuses_other_city_before_db(monkeypatch):
    """Отказ — до оценки и до записи в базу."""
    from krisha import predict as predict_mod

    html = FIXTURE.read_text(encoding="utf-8").replace('"city":"Almaty"', '"city":"Astana"')
    assert '"city":"Astana"' in html
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    def no_db(*a, **k):
        raise AssertionError("объявление не из Алматы не должно доходить до базы")

    monkeypatch.setattr(predict_mod, "get_conn", no_db)

    with pytest.raises(ListingOutsideAlmaty, match="только квартиры в Алматы"):
        predict_mod.predict_from_url(URL, live_vision=False)


def test_api_answers_422_with_reason_and_caches_it(monkeypatch):
    """422 с понятным текстом; повтор той же ссылки не ходит на krisha (негативный кэш)."""
    calls: list[int] = []

    def astana(url, live_vision=False, timeout=None, on_attempt=None):
        calls.append(1)
        raise ListingOutsideAlmaty(OUTSIDE_ALMATY)

    monkeypatch.setattr(predict_gate, "predict_from_url", astana)
    client = TestClient(app)

    first = client.post("/api/predict", json={"url": URL})
    second = client.post("/api/predict", json={"url": URL})

    assert first.status_code == second.status_code == 422
    assert first.json()["detail"] == OUTSIDE_ALMATY
    assert len(calls) == 1


def test_site_shows_server_reason_for_other_city():
    """Фронт узнаёт этот 422 по началу текста сервера и показывает его как есть."""
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    prefix = "Оцениваем только квартиры в Алматы"

    assert OUTSIDE_ALMATY.startswith(prefix)
    assert f"d.startsWith('{prefix}')" in html
    assert len(OUTSIDE_ALMATY) < 160  # detail() длиннее не показывает


def _sent(monkeypatch) -> list[str]:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "tg_call", lambda m, **kw: calls.append((m, kw)) or {"ok": True})
    return calls


def test_bot_says_it_rates_only_almaty(monkeypatch):
    def astana(url, live_vision=True, timeout=None, on_attempt=None):
        raise ListingOutsideAlmaty(OUTSIDE_ALMATY)

    calls = _sent(monkeypatch)
    monkeypatch.setattr(predict_gate, "predict_from_url", astana)

    bot.handle_update({"message": {"chat": {"id": 42}, "text": URL}})

    texts = [kw.get("text", "") for m, kw in calls if m == "sendMessage"]
    assert any("только квартиры в Алматы" in t for t in texts)
    assert not any("Не получилось оценить" in t for t in texts)


def test_track_refuses_other_city_without_storing(tmp_path, monkeypatch):
    """/track объявления не из Алматы: отказ с причиной, в базу и в слежку не попадает."""
    from krisha import config, db, predict, tracking

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "sale.db")
    monkeypatch.setattr(config, "RENT_DB_PATH", tmp_path / "rent.db")
    monkeypatch.setattr(tracking, "TRACKED_PATH", tmp_path / "tracked.json")
    monkeypatch.setattr("krisha.subscriptions._push_to_github", lambda *a, **k: None)
    html = FIXTURE.read_text(encoding="utf-8").replace('"city":"Almaty"', '"city":"Astana"')

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return html

    monkeypatch.setattr(predict, "user_client", lambda **kw: FakeClient())
    stored = []
    monkeypatch.setattr(db, "upsert_listing", lambda *a, **k: stored.append(1))
    calls = _sent(monkeypatch)

    bot.handle_update({"message": {"chat": {"id": 9}, "text": f"/track {URL}"}})

    texts = [kw.get("text", "") for m, kw in calls if m == "sendMessage"]
    assert texts and "только квартиры в Алматы" in texts[-1]
    assert stored == []
    assert tracking.list_tracked(9) == {}
