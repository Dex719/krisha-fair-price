from fastapi.testclient import TestClient

from krisha import predict_gate
from krisha.api import app as app_module
from krisha.api.app import app


def _predict_payload(price_history):
    return {
        "listing_id": 91,
        "url": "https://krisha.kz/a/show/91",
        "title": "История цены",
        "address": "Алматы",
        "actual_price": 48_000_000,
        "fair_price": 47_000_000.0,
        "fair_price_low": 44_000_000.0,
        "fair_price_high": 50_000_000.0,
        "verdict": "FAIR",
        "diff_pct": 2.1,
        "top_factors": [],
        "details": [],
        "complex_details": [],
        "location_details": [],
        "price_history": price_history,
        "days_on_market": None,
        "liquidity": None,
        "text_flags": [],
        "flags_pending": False,
        "duplicate_of": None,
        "photos": [],
        "description": None,
        "analogs": [],
        "scam_risk": None,
        "renovation": None,
    }


def test_predict_api_returns_real_price_history(monkeypatch):
    history = [
        {"observed_at": "2026-06-14 08:00:00", "price": 50_000_000},
        {"observed_at": "2026-07-01 09:30:00", "price": 48_000_000},
    ]
    monkeypatch.setattr(predict_gate, "predict_from_url", lambda url, live_vision=False, timeout=None: _predict_payload(history))
    app_module._rate.clear()

    resp = TestClient(app).post("/api/predict", json={"url": "https://krisha.kz/a/show/91"})

    assert resp.status_code == 200
    assert resp.json()["price_history"] == history


def test_predict_api_keeps_empty_price_history_without_synthetic_rows(monkeypatch):
    monkeypatch.setattr(predict_gate, "predict_from_url", lambda url, live_vision=False, timeout=None: _predict_payload([]))
    app_module._rate.clear()

    resp = TestClient(app).post("/api/predict", json={"url": "https://krisha.kz/a/show/91"})

    assert resp.status_code == 200
    assert resp.json()["price_history"] == []
