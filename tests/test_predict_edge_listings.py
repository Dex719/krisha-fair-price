"""Объявление без цены и снятое объявление (.kiro/specs/predict-edge-listings).

Без цены карточка падала TypeError-ом в build_features (500 на сайте, тишина в
боте); снятое объявление отдавалось как 502 «Не удалось обработать» — фронт
писал «Сервис сейчас не отвечает», хотя объявления просто нет.
"""

from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from krisha import predict_gate
from krisha.api.app import app
from krisha.db import init_db
from krisha.predict import ListingNotFound

FIXTURE = Path(__file__).parent / "fixtures" / "detail_sample.html"


def _listing(**overrides) -> dict:
    from krisha.scraping.detail_parser import parse_detail

    base = parse_detail(FIXTURE.read_text(encoding="utf-8"), "https://krisha.kz/a/show/1")
    assert base is not None
    return {**base, "id": 1, **overrides}


def test_listing_without_price_gets_an_estimate_without_verdict(tmp_path, monkeypatch):
    """AC-1.1: «цена договорная» — оценка есть, вердикта и разницы нет."""
    from krisha import factor_hints
    from krisha import predict as predict_mod

    db = tmp_path / "t.db"
    init_db(db)
    monkeypatch.setattr(predict_mod, "DB_PATH", db)
    monkeypatch.setattr(factor_hints, "DB_PATH", db)

    result = predict_mod.predict_from_listing(_listing(price=None), live_vision=False)

    assert result["fair_price"] > 0
    assert result["actual_price"] is None
    assert result["verdict"] is None
    assert result["diff_pct"] is None


def test_log_price_is_unchanged_for_numeric_prices():
    """AC-1.2: обучение не задето — для числовой цены log_price прежний."""
    from krisha.features import listing_to_frame

    frame = listing_to_frame(_listing(price=50_000_000))
    assert frame["log_price"].iloc[0] == pytest.approx(np.log1p(50_000_000))


def test_404_from_krisha_is_listing_not_found(monkeypatch):
    """AC-2.1: на пользовательском пути None из PoliteClient значит ровно 404."""
    from krisha import predict as predict_mod

    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(404, text="Not Found"))
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    with pytest.raises(ListingNotFound):
        predict_mod.predict_from_url("https://krisha.kz/a/show/1014009074", live_vision=False)


def test_api_answers_404_for_removed_listing_and_caches_it(monkeypatch):
    """AC-2.2: 404 с понятным текстом; повтор не ходит на krisha (негативный кэш)."""
    calls: list[int] = []

    def removed(url, live_vision=False, timeout=None, on_attempt=None):
        calls.append(1)
        raise ListingNotFound("Объявление не найдено — возможно, его уже сняли с продажи")

    monkeypatch.setattr(predict_gate, "predict_from_url", removed)
    client = TestClient(app)
    url = "https://krisha.kz/a/show/1014009074"

    first = client.post("/api/predict", json={"url": url})
    second = client.post("/api/predict", json={"url": url})

    assert first.status_code == 404 and second.status_code == 404
    assert "сняли с продажи" in first.json()["detail"]
    assert len(calls) == 1, "снятое объявление не должно гонять скрейп на каждый запрос"
