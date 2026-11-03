"""Инцидент 2026-11-02: SafeLine закрыл детальные страницы krisha.

Ночной рескрейп оставался зелёным с нулём деталей: 468 не считался баном, и
фаза деталей ~3 ч долбила челлендж до бюджета времени, а алерт о бане не
доходил до Telegram. Здесь — серия «только SafeLine» как BanDetected на
деталях, тревога «детали в плане, докачано 0» и CLI, который при этом
не роняет проход до заливки базы.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from krisha.db import get_conn, init_db
from krisha.scraping import client as client_mod
from krisha.scraping import rescrape
from krisha.scraping.client import BanDetected, PoliteClient
from krisha.scraping.rescrape import DETAIL_WAF_STREAK, shard_urls, sweep

CHALLENGE_BODY = (
    '<!DOCTYPE html><html><head><meta charset="utf-8">'
    '<link rel="icon" href="/.safeline/static/favicon.png" type="image/png">'
    '<title id="slg-title"></title></head><body>_pow</body></html>'
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(rescrape, "DATA_DIR", tmp_path)
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)


class _Resp:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _Session:
    """Сессия httpx-двойник: ответ по URL из общего «сайта»."""

    def __init__(self, site):
        self.site = site

    def get(self, url):
        return self.site(url)

    def close(self):
        pass


def _polite(site, *, waf_streak_threshold=None, ban_streak_threshold=3) -> PoliteClient:
    """Настоящий PoliteClient (логика попыток/серий), сеть — двойник."""
    client = PoliteClient(
        delay_range=(0, 0),
        max_retries=3,
        throttle_wait_s=0,
        challenge_wait_s=0,
        ban_streak_threshold=ban_streak_threshold,
        waf_streak_threshold=waf_streak_threshold,
    )
    client._new_session = lambda seed=False: _Session(site)
    client._client = _Session(site)
    return client


def _scripted(per_url: dict[str, list[int]], default: int = 468):
    """Сайт: на каждый URL — своя очередь кодов; кончилась — default."""
    queues = {url: list(codes) for url, codes in per_url.items()}

    def site(url):
        q = queues.get(url)
        code = q.pop(0) if q else default
        body = CHALLENGE_BODY if code in (468, 4031) else "ok"
        return _Resp(403 if code == 4031 else code, body)

    return site


# ---------------------------------------------------------------- client --- #

SAFELINE_403 = 4031  # 403 со страницей SafeLine (блок), в отличие от голого 403


def test_streak_of_all_safeline_urls_raises_ban():
    client = _polite(_scripted({}, default=468), waf_streak_threshold=3)

    assert client.get("http://x/1") is None
    assert client.get("http://x/2") is None
    with pytest.raises(BanDetected, match="SafeLine"):
        client.get("http://x/3")
    assert client.counters["http_468"] == 9  # 3 URL × 3 попытки


def test_mixed_challenge_and_safeline_403_block_counts_as_safeline():
    site = _scripted(
        {
            "http://x/1": [SAFELINE_403] * 3,
            "http://x/2": [468, SAFELINE_403, 468],
            "http://x/3": [468] * 3,
        }
    )
    client = _polite(site, waf_streak_threshold=3, ban_streak_threshold=99)

    client.get("http://x/1")
    client.get("http://x/2")
    with pytest.raises(BanDetected):
        client.get("http://x/3")


def test_stray_challenge_among_successes_never_bans():
    """468 на одной попытке, потом 200 — успех; он и 404 серию сбрасывают."""
    site = _scripted(
        {
            "http://x/1": [468, 468, 468],
            "http://x/2": [468, 468, 468],
            "http://x/3": [468, 200],  # одиночный челлендж, затем страница
            "http://x/4": [468, 468, 468],
            "http://x/5": [468, 468, 468],
            "http://x/6": [404],
            "http://x/7": [468, 468, 468],
            "http://x/8": [468, 468, 468],
        }
    )
    client = _polite(site, waf_streak_threshold=3)

    results = [client.get(f"http://x/{i}") for i in range(1, 9)]

    assert results[2] == "ok"
    assert client._waf_streak == 2


def test_challenge_mixed_with_timeout_is_not_all_safeline():
    import httpx

    calls = iter([468, "timeout", 468] * 5)

    def site(url):
        code = next(calls)
        if code == "timeout":
            raise httpx.ReadTimeout("slow")
        return _Resp(code, CHALLENGE_BODY)

    client = _polite(site, waf_streak_threshold=1)
    for i in range(5):
        assert client.get(f"http://x/{i}") is None  # ни одного BanDetected


def test_threshold_is_off_by_default():
    """Выдача и пользовательский путь: порога нет — прежнее поведение (None)."""
    client = _polite(_scripted({}, default=468))
    for i in range(10):
        assert client.get(f"http://x/{i}") is None


# ----------------------------------------------------------------- sweep --- #


def _card(lid: int, price: int = 10_000_000) -> str:
    return (
        f'<div data-id="{lid}" class="a-card"><a href="/a/show/{lid}">x</a>'
        f'<span class="a-card__price">{price:,} ₸</span></div>'.replace(",", " ")
    )


def _listing(lid: int) -> dict:
    return {
        "id": lid,
        "url": f"https://krisha.kz/a/show/{lid}",
        "price": 10_000_000,
        "title": "test",
        "rooms": 2,
        "area": 60.0,
    }


LOTS = list(range(101, 109))  # 8 новых лотов в первом шарде


def _krisha(detail_codes: dict[int, list[int]], default_detail: int = 468):
    """Выдача отвечает 200, детальные страницы — по сценарию."""
    first_url = shard_urls()[0][1]
    search = {first_url: "".join(_card(lid) for lid in LOTS)}
    details = _scripted(
        {f"https://krisha.kz/a/show/{lid}": codes for lid, codes in detail_codes.items()},
        default=default_detail,
    )
    detail_calls: list[str] = []

    def site(url):
        if "/a/show/" in url:
            detail_calls.append(url)
            return details(url)
        return _Resp(200, search.get(url, "<html></html>"))

    return site, detail_calls


@pytest.fixture
def harness(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    init_db(db)
    bans: list[str] = []
    zero: list[tuple[int, str]] = []
    monkeypatch.setattr(rescrape, "_alert_ban", lambda exc: bans.append(str(exc)))
    monkeypatch.setattr(rescrape, "_alert_zero_details", lambda n, note: zero.append((n, note)))
    monkeypatch.setattr(
        rescrape,
        "parse_detail",
        lambda html, url: _listing(int(url.rsplit("/", 1)[1])) if html == "ok" else None,
    )

    def run(site, max_new=None):
        built = []

        def factory(**kw):
            built.append(_polite(site))
            return built[0]

        monkeypatch.setattr(rescrape, "PoliteClient", factory)
        cap = len(LOTS) if max_new is None else max_new
        stats = sweep(max_pages=1, max_new_details=cap, max_refresh=0, db_path=db)
        return stats, built[0]

    return db, run, bans, zero


def test_safeline_streak_on_details_is_a_details_ban(harness):
    db, run, bans, zero = harness
    site, detail_calls = _krisha({}, default_detail=468)

    stats, client = run(site)

    assert stats["banned"] is True
    assert stats["banned_phase"] == "details"
    assert len(bans) == 1 and "SafeLine" in bans[0]
    # прервались на DETAIL_WAF_STREAK-м лоте, а не долбили все 8 (×3 попытки)
    assert len(detail_calls) == DETAIL_WAF_STREAK * client.max_retries
    # выдача сохранена: все 8 лотов увидены и записаны (sighting)
    assert stats["found_in_search"] == len(LOTS)
    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == len(LOTS)
    # и тревога «0 деталей» тоже ушла
    assert stats["details_fetched"] == 0
    assert stats["zero_details"] is True
    assert zero and zero[0][0] == len(LOTS)


def test_safeline_403_blocks_on_details_also_ban(harness):
    _db, run, bans, _zero = harness
    site, _ = _krisha({}, default_detail=SAFELINE_403)

    stats, _client = run(site)

    assert stats["banned_phase"] == "details"
    assert len(bans) == 1


def test_mixed_challenge_and_success_does_not_ban(harness):
    _db, run, bans, zero = harness
    # чередуем: два лота «только 468», лот со страницей после одиночного 468, …
    scenario = {
        101: [468, 468, 468],
        102: [468, 468, 468],
        103: [468, 200],
        104: [468, 468, 468],
        105: [468, 468, 468],
        106: [200],
        107: [468, 468, 468],
        108: [468, 468, 468],
    }
    site, detail_calls = _krisha(scenario)

    stats, _client = run(site)

    assert stats["banned"] is False
    assert bans == []
    assert stats["details_fetched"] == 2
    assert stats["zero_details"] is False
    assert zero == []
    assert len(detail_calls) == 3 * 6 + 2 + 1  # все 8 лотов пройдены


def test_zero_details_without_ban_still_alarms(harness):
    """Все детали 404 — не бан, но 0 из плана: ERROR + алерт, проход не красный."""
    _db, run, bans, zero = harness
    site, _ = _krisha({}, default_detail=404)

    stats, _client = run(site)

    assert stats["banned"] is False
    assert bans == []
    assert stats["details_planned"] == len(LOTS)
    assert stats["zero_details"] is True
    assert len(zero) == 1


def test_no_alarm_when_nothing_was_planned(harness):
    """--max-new 0 и без обновления устаревших — плана деталей нет, тревоги нет."""
    _db, run, _bans, zero = harness
    site, detail_calls = _krisha({})

    stats, _client = run(site, max_new=0)

    assert stats["details_planned"] == 0
    assert stats["zero_details"] is False
    assert zero == []
    assert detail_calls == []


def test_zero_details_alert_sends_admin_message(monkeypatch):
    sent = []
    monkeypatch.setenv("TG_ADMIN_CHAT_ID", "42")
    monkeypatch.setattr("krisha.bot.tg_call", lambda method, **kw: sent.append((method, kw)))

    rescrape._alert_zero_details(7544, "бан на деталях: True")

    assert len(sent) == 1
    method, payload = sent[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == 42
    assert "0 деталей из 7544" in payload["text"]


def test_zero_details_alert_skips_without_chat(monkeypatch):
    monkeypatch.delenv("TG_ADMIN_CHAT_ID", raising=False)
    monkeypatch.setattr("krisha.bot.tg_call", lambda *a, **k: pytest.fail("без чата слать нельзя"))
    rescrape._alert_zero_details(1, "x")


# ------------------------------------------------------------------- CLI --- #

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "rescrape.py"
_spec = importlib.util.spec_from_file_location("rescrape_cli_safeline", _SCRIPT_PATH)
rescrape_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rescrape_cli)


def test_cli_does_not_fail_before_upload_on_details_ban_with_zero_details(monkeypatch):
    """--fail-empty: бан на деталях + ноль деталей — выход 0, иначе шаг Rescrape
    красный и `Upload DB to release` (без if: always()) не выполняется."""
    stats = {
        "found_in_search": 40000,
        "suspicious": False,
        "banned": True,
        "banned_phase": "details",
        "details_fetched": 0,
        "details_planned": 4500,
        "zero_details": True,
    }
    monkeypatch.setattr(rescrape_cli, "sweep", lambda **kw: stats)
    monkeypatch.setattr(sys, "argv", ["rescrape.py", "--fail-empty", "--fail-below", "8000"])
    rescrape_cli.main()  # не должно поднять SystemExit
