"""Состояние бота живёт в приватном репозитории данных, а не в репо кода."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from krisha import subscriptions as subs_mod
from krisha import usage

# Настоящая реализация, снятая до того, как autouse-фикстура conftest её подменит.
_REAL_PUSH = subs_mod._push_to_github


class _Resp:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.fixture()
def no_tokens(monkeypatch):
    for name in ("KRISHA_DB_TOKEN", "GITHUB_PAT", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_pull_state_writes_files_and_skips_missing(tmp_path, monkeypatch, no_tokens):
    """200 — файл кладётся как есть (зашифрованная обёртка тоже), 404 — файла
    в репо ещё нет и это не ошибка, 5xx и сетевой сбой — ошибки."""
    monkeypatch.setenv("KRISHA_DB_TOKEN", "tok")
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "k")
    monkeypatch.setattr(subs_mod, "DATA_DIR", tmp_path)
    wrapped, encrypted = subs_mod._encode_payload({"42": {"rooms": 2}}, encrypt=True)
    assert encrypted
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append((url, headers["Authorization"], headers["Accept"]))
        name = url.rsplit("/", 1)[-1]
        if name == "subscriptions.json":
            return _Resp(200, wrapped)
        if name == "usage_stats.json":
            return _Resp(500)
        if name == "tracked.json":
            raise httpx.ConnectError("down")
        return _Resp(404)

    monkeypatch.setattr(subs_mod.httpx, "get", fake_get)
    pulled, failed = subs_mod.pull_state()

    assert (pulled, failed) == (1, 2)
    assert (tmp_path / "subscriptions.json").read_text(encoding="utf-8") == wrapped
    assert not (tmp_path / "usage_stats.json").exists()
    assert len(seen) == len(subs_mod.STATE_FILES)
    url, auth, accept = seen[0]
    assert url == (
        f"https://api.github.com/repos/{subs_mod.STATE_REPO}/contents/data/subscriptions.json"
    )
    assert auth == "Bearer tok"
    assert accept == "application/vnd.github.raw+json"


def test_pull_state_without_token_does_nothing(tmp_path, monkeypatch, no_tokens):
    monkeypatch.setattr(subs_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(subs_mod.httpx, "get", lambda *a, **k: pytest.fail("сеть без токена"))
    assert subs_mod.pull_state() == (0, 0)


def test_state_token_precedence(monkeypatch, no_tokens):
    monkeypatch.setenv("GITHUB_TOKEN", "actions")
    assert subs_mod._state_token() == "actions"
    monkeypatch.setenv("GITHUB_PAT", "pat")
    assert subs_mod._state_token() == "pat"
    monkeypatch.setenv("KRISHA_DB_TOKEN", "data")
    assert subs_mod._state_token() == "data"


def test_state_repo_defaults_to_private_data_repo():
    """Состояние не должно уезжать обратно в публичный репо кода."""
    assert subs_mod.STATE_REPO == "Dex719/krisha-db"


def test_push_goes_to_state_repo(tmp_path, monkeypatch, no_tokens):
    monkeypatch.setattr(subs_mod, "_push_to_github", _REAL_PUSH)
    monkeypatch.setenv("KRISHA_DB_TOKEN", "tok")
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    urls = []

    def fake_get(url, headers=None, timeout=None):
        urls.append(url)
        return _Resp(404)

    def fake_put(url, headers=None, json=None, timeout=None):
        urls.append(url)
        assert headers["Authorization"] == "Bearer tok"
        return _Resp(201)

    monkeypatch.setattr(subs_mod.httpx, "get", fake_get)
    monkeypatch.setattr(subs_mod.httpx, "put", fake_put)
    subs_mod.save_json_state(tmp_path / "channel_posted.json", [1, 2], "msg", encrypt=False)

    expected = f"https://api.github.com/repos/{subs_mod.STATE_REPO}/contents/data/channel_posted.json"
    assert urls == [expected, expected]


def test_cli_pull_fails_on_incomplete_state(tmp_path, monkeypatch, no_tokens):
    """Алерты без channel_posted заново запостят опубликованное — поэтому
    неполная загрузка роняет шаг воркфлоу, а не проходит молча."""
    monkeypatch.setattr(subs_mod, "DATA_DIR", tmp_path)
    assert subs_mod.main(["--pull"]) == 1  # нет токена

    monkeypatch.setenv("KRISHA_DB_TOKEN", "tok")
    monkeypatch.setattr(subs_mod.httpx, "get", lambda *a, **k: _Resp(502))
    assert subs_mod.main(["--pull"]) == 1

    monkeypatch.setattr(subs_mod.httpx, "get", lambda *a, **k: _Resp(404))
    assert subs_mod.main(["--pull"]) == 0


def test_usage_flush_prunes_days_restored_by_merge(monkeypatch):
    """Слияние с файлом возвращало дни, уже вычищенные из памяти, и KEEP_DAYS
    не действовал: в файле жили дни с 03.07 вместе с хэшами id без соли."""
    now = datetime.now(timezone.utc)
    old_day = (now - timedelta(days=usage.KEEP_DAYS + 30)).strftime("%Y-%m-%d")
    fresh_day = now.strftime("%Y-%m-%d")
    usage.USAGE_PATH.write_text(
        json.dumps({"days": {old_day: {"site": 5, "bot_users": ["d6986db552"]}}}), encoding="utf-8"
    )
    saved = {}
    monkeypatch.setattr(
        subs_mod, "save_json_state",
        lambda path, data, message, encrypt=True, deleted_keys=None: saved.setdefault("data", data),
    )

    usage._flush({"days": {fresh_day: {"site": 1}}})

    assert set(saved["data"]["days"]) == {fresh_day}


def test_pull_state_refuses_unreadable_state(tmp_path, monkeypatch, no_tokens):
    """Файл есть, но не расшифровывается (сменился ключ): раньше он молча
    читался как «подписчиков нет» — алерты и слежка выключались без следа.
    Теперь это сбой загрузки: локальную копию не трогаем, админу — сообщение."""
    monkeypatch.setenv("KRISHA_DB_TOKEN", "tok")
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "старый")
    monkeypatch.setattr(subs_mod, "DATA_DIR", tmp_path)
    wrapped, _ = subs_mod._encode_payload({"42": {"rooms": 2}}, encrypt=True)
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "новый")
    (tmp_path / "subscriptions.json").write_text("локальная копия", encoding="utf-8")
    alerts = []
    monkeypatch.setattr(subs_mod, "_alert_admin", alerts.append)
    monkeypatch.setattr(subs_mod.httpx, "get", lambda url, headers=None, timeout=None: _Resp(200, wrapped))

    pulled, failed = subs_mod.pull_state(("subscriptions.json",))

    assert (pulled, failed) == (0, 1)
    assert (tmp_path / "subscriptions.json").read_text(encoding="utf-8") == "локальная копия"
    assert alerts and "не расшифровывается" in alerts[0]
