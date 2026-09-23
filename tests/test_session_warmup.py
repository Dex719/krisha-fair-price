"""Прогрев сессии krisha при старте (.kiro/specs/krisha-session-warmup).

После рестарта хранилище липких кук пустое, и первая сессия тянет жребий
SafeLine; с IP Hugging Face он часто проигрышный — смоук после выката b999257
прошёл предикт только с 3-й из 3 попыток. Жребий забирает фоновый запрос при
старте, а не первый человек.
"""

import threading

import httpx
import pytest

from krisha import predict as predict_mod
from krisha import predict_gate
from krisha.api import app as app_module
from krisha.api import metrics
from krisha.db import get_conn, init_db
from krisha.scraping.client import StickyCookies

SAFELINE_403 = (
    '<!DOCTYPE html><html><head><link rel="icon" href="/.safeline/static/favicon.png">'
    '<title id="slg-title"></title></head><body>blocked</body></html>'
)


@pytest.fixture
def fresh_store(monkeypatch):
    store = StickyCookies()
    monkeypatch.setattr(predict_mod, "USER_COOKIES", store)
    monkeypatch.setattr("krisha.scraping.client.time.sleep", lambda _s: None)
    return store


def _krisha(monkeypatch, responses):
    """Все httpx.Client в тесте отвечают по очереди из responses."""
    queue = list(responses)
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: queue.pop(0) if len(queue) > 1 else queue[0])
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))


def test_warmup_absorbs_safeline_blocks_and_keeps_the_good_session(monkeypatch, fresh_store):
    _krisha(monkeypatch, [
        httpx.Response(403, text=SAFELINE_403),
        httpx.Response(403, text=SAFELINE_403),
        httpx.Response(200, text="ok", headers={"set-cookie": "kraid=A9; Path=/"}),
    ])

    assert predict_gate.warm_session("https://krisha.kz/a/show/1") is True

    assert [c.value for c in fresh_store.cookies().jar] == ["A9"], "первый человек начнёт с ведра A"
    counters = metrics.snapshot()["counters"]
    assert counters["session_warmup_ok"] == 1
    assert counters["scrape_waf_block"] == 2


def test_failed_warmup_never_raises(monkeypatch, fresh_store):
    _krisha(monkeypatch, [httpx.Response(403, text=SAFELINE_403)])

    assert predict_gate.warm_session("https://krisha.kz/a/show/1") is False

    assert len(fresh_store) == 0
    assert metrics.snapshot()["counters"]["session_warmup_failed"] == 1


def _db_with_listing(tmp_path):
    db = tmp_path / "k.db"
    init_db(db)
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO listings (id, url, is_active, last_seen) "
            "VALUES (7, 'https://krisha.kz/a/show/7', 1, datetime('now'))"
        )
    return db


def test_startup_warms_the_session_with_a_listing_from_the_demo_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("KRISHA_SESSION_WARMUP", "1")
    monkeypatch.setattr(app_module, "DB_PATH", _db_with_listing(tmp_path))
    called: list[str] = []
    done = threading.Event()

    def fake_warm(url):
        called.append(url)
        done.set()
        return True

    monkeypatch.setattr(predict_gate, "warm_session", fake_warm)

    app_module._start_session_warmup()

    assert done.wait(5), "прогрев должен запуститься в фоне"
    assert called == ["https://krisha.kz/a/show/7"]


@pytest.mark.parametrize("env, has_db", [("0", True), ("1", False)])
def test_startup_skips_warmup_when_disabled_or_no_db(tmp_path, monkeypatch, env, has_db):
    monkeypatch.setenv("KRISHA_SESSION_WARMUP", env)
    db = _db_with_listing(tmp_path) if has_db else tmp_path / "нет.db"
    monkeypatch.setattr(app_module, "DB_PATH", db)
    called: list[str] = []
    monkeypatch.setattr(predict_gate, "warm_session", lambda url: called.append(url) or True)

    app_module._start_session_warmup()

    assert called == []
