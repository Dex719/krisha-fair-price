"""Аудит 24.10: слежка при двух воркерах и записи из Actions, большие файлы в GitHub.

GitHub здесь — словарь в памяти с sha и 409 на устаревший sha; сети нет.
"""

import base64
import hashlib
import json

import pytest

from krisha import subscriptions as subs_mod
from krisha import tracking

# conftest глушит пуш в GitHub для всех тестов; здесь нужен настоящий
_REAL_PUSH = subs_mod._push_to_github


class FakeRepo:
    """Contents API на один репозиторий: GET (json/raw) и PUT с проверкой sha."""

    def __init__(self, big: bool = False):
        self.files: dict[str, tuple[str, str]] = {}  # url -> (sha, текст)
        self.big = big  # отдавать как файл > 1 МБ: content пустой, encoding none
        self.puts: list[str] = []

    def text(self, name: str) -> str:
        return next(t for url, (_, t) in self.files.items() if url.endswith(name))

    def get(self, url, headers=None, timeout=None):
        if url not in self.files:
            return _Resp(404, {"message": "Not Found"})
        sha, text = self.files[url]
        if (headers or {}).get("Accept") == "application/vnd.github.raw+json":
            return _Resp(200, text=text)
        if self.big:
            return _Resp(200, {"sha": sha, "content": "", "encoding": "none", "size": 2_000_000})
        return _Resp(200, {"sha": sha, "encoding": "base64", "size": len(text),
                           "content": base64.b64encode(text.encode()).decode()})

    def put(self, url, headers=None, json=None, timeout=None):
        current = self.files.get(url)
        if current is not None and json.get("sha") != current[0]:
            return _Resp(409, {"message": "conflict"})
        text = base64.b64decode(json["content"]).decode()
        self.files[url] = (hashlib.sha1(text.encode()).hexdigest(), text)
        self.puts.append(url)
        return _Resp(201, {})


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(subs_mod, "_push_to_github", _REAL_PUSH)
    monkeypatch.setattr(subs_mod.httpx, "get", fake.get)
    monkeypatch.setattr(subs_mod.httpx, "put", fake.put)
    monkeypatch.setattr(subs_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(subs_mod, "_alert_admin", lambda text: pytest.fail(f"алерт админу: {text}"))
    monkeypatch.setenv("KRISHA_DB_TOKEN", "t")
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "test-key")
    return fake


def _remote_tracked(repo) -> dict:
    return subs_mod._decode_payload(repo.text("tracked.json"))


def _worker(tmp_path, name):
    """Локальная копия одного воркера uvicorn: свой файл, общий удалённый репо."""
    d = tmp_path / name
    d.mkdir()
    return d / "tracked.json"


# --- п.4: два воркера ----------------------------------------------------------

def test_two_workers_tracking_same_chat_keep_both_lots(repo, tmp_path):
    a, b = _worker(tmp_path, "a"), _worker(tmp_path, "b")
    # оба стартовали с пустой копией; «Следить» на разных лотах ушли в разные процессы
    assert tracking.add_tracked(42, 1, 100, "Первый", path=a) == (True, None)
    assert tracking.add_tracked(42, 2, 200, "Второй", path=b) == (True, None)

    assert set(_remote_tracked(repo)["42"]) == {"1", "2"}, "второй писатель затёр лот первого"


def test_untrack_in_one_worker_does_not_come_back_from_the_other(repo, tmp_path):
    a, b = _worker(tmp_path, "a"), _worker(tmp_path, "b")
    tracking.add_tracked(42, 1, 100, None, path=a)
    tracking.add_tracked(42, 2, 200, None, path=a)
    b.write_text(a.read_text(encoding="utf-8"), encoding="utf-8")  # B видел оба лота

    assert tracking.remove_tracked(42, 1, path=a) == 1
    # B по своей устаревшей копии добавляет третий лот — первый не воскресает
    assert tracking.add_tracked(42, 3, 300, None, path=b) == (True, None)
    assert set(_remote_tracked(repo)["42"]) == {"2", "3"}

    # последний лот чата ушёл — чат исчезает с сервера целиком
    tracking.remove_tracked(42, 2, path=a)
    tracking.remove_tracked(42, 3, path=a)
    assert "42" not in _remote_tracked(repo)


def test_limit_holds_after_merge_of_two_workers(repo, tmp_path):
    a, b = _worker(tmp_path, "a"), _worker(tmp_path, "b")
    for i in range(tracking.MAX_TRACKED_PER_CHAT - 1):
        assert tracking.add_tracked(42, 100 + i, None, None, path=a)[0]
    b.write_text(a.read_text(encoding="utf-8"), encoding="utf-8")  # у обоих по 9 лотов

    assert tracking.add_tracked(42, 900, None, None, path=a) == (True, None)
    # B видит 9 лотов и пропускает свой, но после слияния лот лишний
    assert tracking.add_tracked(42, 901, None, None, path=b) == (False, "limit")

    lots = _remote_tracked(repo)["42"]
    assert len(lots) == tracking.MAX_TRACKED_PER_CHAT and "900" in lots and "901" not in lots


def test_merge_lot_level_keys_unit():
    remote = {"42": {"1": {"p": 1}}, "7": {"5": {"p": 5}}}
    local = {"42": {"2": {"p": 2}, "1": {"p": 999}}, "7": {}}
    merged = subs_mod._merge_remote(local, remote, {"7:5"}, touched_keys={"42:2"})
    assert merged == {"42": {"1": {"p": 1}, "2": {"p": 2}}}
    # update_only: лот, которого на сервере уже нет, не воскрешается
    merged = subs_mod._merge_remote(local, remote, None, touched_keys={"42:2", "42:1"},
                                    update_only=True)
    assert merged["42"] == {"1": {"p": 999}}
    # remote не мутируется
    assert remote == {"42": {"1": {"p": 1}}, "7": {"5": {"p": 5}}}


# --- п.5: Actions пишет устаревшую копию -------------------------------------------

def test_nightly_pass_does_not_roll_back_track_made_during_the_step(repo, tmp_path):
    from krisha.db import init_db, upsert_listing

    db = tmp_path / "krisha.db"
    init_db(db)
    upsert_listing({"id": 111, "url": "https://krisha.kz/a/show/111", "title": "Кв", "price": 47_000_000,
                    "area": 60.0, "rooms": 2, "source": "test"}, db_path=db)
    upsert_listing({"id": 222, "url": "https://krisha.kz/a/show/222", "title": "Кв2", "price": 30_000_000,
                    "area": 40.0, "rooms": 1, "source": "test"}, db_path=db)

    bot_side, actions = _worker(tmp_path, "space"), _worker(tmp_path, "actions")
    tracking.add_tracked(42, 111, 50_000_000, "Кв", path=bot_side)
    tracking.add_tracked(42, 222, 35_000_000, "Кв2", path=bot_side)
    # Actions скачали состояние в начале шага…
    actions.write_text(bot_side.read_text(encoding="utf-8"), encoding="utf-8")
    # …а за время рассылки пользователь снял 222 со слежки и взял 333
    tracking.remove_tracked(42, 222, path=bot_side)
    tracking.add_tracked(42, 333, 10_000_000, "Новый", path=bot_side)

    updates = tracking.check_tracked_updates(db_path=db, path=actions, persist=False)
    assert {chat for chat, _ in updates} == {42}
    tracking.check_tracked_updates(db_path=db, path=actions, persist=True, only_chats={42})

    lots = _remote_tracked(repo)["42"]
    assert set(lots) == {"111", "333"}, "запись Actions откатила /untrack или /track"
    assert lots["111"]["price"] == 47_000_000  # новая цена сохранена


# --- п.11: файлы больше 1 МБ -----------------------------------------------------

def test_push_reads_big_remote_file_raw_and_merges(repo, tmp_path):
    repo.big = True
    url = f"{subs_mod._GH_API}/repos/{subs_mod.STATE_REPO}/contents/data/subscriptions.json"
    remote_payload, _ = subs_mod._encode_payload({"111": {"rooms": 1}}, True)
    repo.files[url] = ("sha-big", remote_payload)

    subs_mod.save_json_state(tmp_path / "subscriptions.json", {"222": {"rooms": 2}}, "msg",
                             touched_keys={"222"})

    assert subs_mod._decode_payload(repo.text("subscriptions.json")) == {
        "111": {"rooms": 1}, "222": {"rooms": 2},
    }, "Contents API отдал content='' — слияние пропущено, сервер затёрт"


def test_push_refuses_when_raw_read_of_big_file_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(subs_mod, "_push_to_github", _REAL_PUSH)
    monkeypatch.setenv("KRISHA_DB_TOKEN", "t")
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "test-key")
    alerts, puts = [], []
    monkeypatch.setattr(subs_mod, "_alert_admin", alerts.append)

    def get(url, headers=None, timeout=None):
        if headers.get("Accept") == "application/vnd.github.raw+json":
            return _Resp(502, {"message": "bad gateway"})
        return _Resp(200, {"sha": "s", "content": "", "encoding": "none", "size": 2_000_000})

    monkeypatch.setattr(subs_mod.httpx, "get", get)
    monkeypatch.setattr(subs_mod.httpx, "put", lambda *a, **k: puts.append(k) or _Resp(201, {}))

    subs_mod.save_json_state(tmp_path / "subscriptions.json", {"222": {}}, "msg")
    assert puts == [] and alerts


# --- п.7: мёртвый чат и переезд группы ----------------------------------------------

def test_forget_and_migrate_chat_state(monkeypatch, tmp_path):
    for env in ("KRISHA_DB_TOKEN", "GITHUB_PAT", "GITHUB_TOKEN"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(subs_mod, "SUBSCRIPTIONS_PATH", tmp_path / "subscriptions.json")
    monkeypatch.setattr(tracking, "TRACKED_PATH", tmp_path / "tracked.json")

    subs_mod.set_subscription(-100, {"rooms": 2})
    subs_mod.set_subscription(7, {"rooms": None})
    tracking.add_tracked(-100, 1, 100, None)
    tracking.add_tracked(7, 2, 200, None)

    subs_mod.migrate_chat(-100, -100555)
    assert "-100" not in subs_mod.load_subscriptions()
    assert subs_mod.load_subscriptions()["-100555"]["rooms"] == 2
    assert tracking.list_tracked(-100555) and not tracking.list_tracked(-100)

    subs_mod.forget_chat(7)
    assert "7" not in subs_mod.load_subscriptions() and not tracking.list_tracked(7)
