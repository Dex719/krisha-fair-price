"""Шифрование state-файлов (subscriptions/tracked) в публичном репозитории."""

import json
import json as _json
import os

import pytest

from krisha import subscriptions as subs_mod
from krisha.subscriptions import load_json_state, save_json_state

# Настоящая реализация, снятая до того, как autouse-фикстура её подменит:
# тесты слияния должны гонять именно её (с заглушенным httpx).
_REAL_PUSH = subs_mod._push_to_github


@pytest.fixture(autouse=True)
def _no_github_push(monkeypatch):
    """Не ходим в GitHub Contents API из тестов."""
    monkeypatch.setattr(subs_mod, "_push_to_github", lambda *a, **k: None)


def test_plaintext_without_key(tmp_path, monkeypatch):
    """Нет ключа (локальная разработка) — сохраняем и читаем открытый JSON."""
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    path = tmp_path / "state.json"
    save_json_state(path, {"123": {"rooms": 2}}, "msg")
    on_disk = json.loads(path.read_text())
    assert on_disk == {"123": {"rooms": 2}}
    assert load_json_state(path) == {"123": {"rooms": 2}}


def test_plaintext_pii_is_not_pushed(tmp_path, monkeypatch):
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    pushed = []
    monkeypatch.setattr(subs_mod, "_push_to_github", lambda *args: pushed.append(args))
    path = tmp_path / "state.json"

    save_json_state(path, {"123": {"rooms": 2}}, "msg", encrypt=True)

    assert pushed == []
    # Права 0600 — POSIX-свойство: на Windows os.chmod умеет только флаг
    # «только для чтения», st_mode всегда остаётся 0666, и проверка ломала
    # весь локальный прогон тестов у разработчика на Windows.
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_encrypted_roundtrip(tmp_path, monkeypatch):
    """С ключом файл на диске не содержит chat_id, а load расшифровывает."""
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "test-secret")
    path = tmp_path / "state.json"
    data = {"424242": {"rooms": 3, "max_price": 45_000_000}}
    save_json_state(path, data, "msg")

    raw = path.read_text()
    assert "424242" not in raw, "chat_id не должен лежать в файле открыто"
    assert "_encrypted" in json.loads(raw)
    assert load_json_state(path) == data


def test_key_from_bot_token(tmp_path, monkeypatch):
    """Без STATE_ENCRYPTION_KEY ключ выводится из TELEGRAM_BOT_TOKEN."""
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-token")
    path = tmp_path / "state.json"
    save_json_state(path, {"777": {}}, "msg")
    assert "777" not in path.read_text()
    assert load_json_state(path) == {"777": {}}


def test_wrong_key_returns_none(tmp_path, monkeypatch):
    """Сменился ключ (ротация токена) — не падаем, просто теряем состояние."""
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "old-key")
    path = tmp_path / "state.json"
    save_json_state(path, {"1": {}}, "msg")
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "new-key")
    assert load_json_state(path) is None


def test_legacy_plaintext_readable_with_key(tmp_path, monkeypatch):
    """Старый plaintext-файл читается и при включённом шифровании (миграция)."""
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "test-secret")
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"555": {"rooms": 1}}))
    assert load_json_state(path) == {"555": {"rooms": 1}}


def test_tracking_uses_encryption(tmp_path, monkeypatch):
    """add_tracked/list_tracked работают поверх шифрованного файла."""
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "test-secret")
    from krisha.tracking import add_tracked, list_tracked

    path = tmp_path / "tracked.json"
    ok, reason = add_tracked(99001, 123456789, 45_000_000, "Тестовый лот", path=path)
    assert ok and reason is None
    assert "99001" not in path.read_text()
    lots = list_tracked(99001, path=path)
    assert "123456789" in lots and lots["123456789"]["price"] == 45_000_000


def test_push_merges_concurrent_remote_changes(monkeypatch, tmp_path):
    """issue #111: у state-файлов два независимых писателя — Space (команды
    бота) и GitHub Actions. Раньше sha читался прямо перед PUT, поэтому
    конфликт, который должен был дать 409, превращался в тихую перезапись:
    подписка, оформленная другим писателем, исчезала без следа."""
    import base64

    monkeypatch.setattr(subs_mod, "_push_to_github", _REAL_PUSH)
    monkeypatch.setenv("GITHUB_PAT", "t")
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    # На сервере уже лежит подписка чата 777, которой мы не видели.
    remote = json.dumps({"777": {"rooms": 1}})
    put_bodies = []

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        subs_mod.httpx, "get",
        lambda *a, **k: _Resp({"sha": "abc", "content": base64.b64encode(remote.encode()).decode()}),
    )

    def fake_put(url, headers=None, json=None, timeout=None):
        put_bodies.append(json)
        return _Resp({})

    monkeypatch.setattr(subs_mod.httpx, "put", fake_put)

    path = tmp_path / "subscriptions.json"
    save_json_state(path, {"111": {"rooms": 2}}, "msg", encrypt=False)

    pushed = _json.loads(base64.b64decode(put_bodies[0]["content"]).decode())
    assert pushed == {"777": {"rooms": 1}, "111": {"rooms": 2}}, "чужая подписка не должна пропасть"


def test_push_honours_intentional_deletion(monkeypatch, tmp_path):
    """Обратная сторона слияния: отписка не должна «воскресать» с сервера."""
    import base64

    monkeypatch.setattr(subs_mod, "_push_to_github", _REAL_PUSH)
    monkeypatch.setenv("GITHUB_PAT", "t")
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    remote = json.dumps({"777": {"rooms": 1}, "111": {"rooms": 2}})
    put_bodies = []

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        subs_mod.httpx, "get",
        lambda *a, **k: _Resp({"sha": "abc", "content": base64.b64encode(remote.encode()).decode()}),
    )
    monkeypatch.setattr(
        subs_mod.httpx, "put",
        lambda url, headers=None, json=None, timeout=None: (
            put_bodies.append(json) or _Resp({})
        ),
    )

    path = tmp_path / "subscriptions.json"
    save_json_state(path, {"777": {"rooms": 1}}, "msg", encrypt=False, deleted_keys={"111"})

    pushed = _json.loads(base64.b64decode(put_bodies[0]["content"]).decode())
    assert pushed == {"777": {"rooms": 1}}, "удалённый нами ключ не должен вернуться"


def _resp_factory(remote_text, sha="abc"):
    import base64

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    return _Resp({"sha": sha, "content": base64.b64encode(remote_text.encode()).decode()})


def test_push_refuses_to_overwrite_unreadable_remote(monkeypatch, tmp_path):
    """issue #150: файл на сервере ЕСТЬ, но не расшифровывается (сменился ключ).

    Раньше слияние просто пропускалось и уходил PUT с нашим payload и чужим
    sha — свежеподнятый Space с пустым состоянием одной командой стирал всех
    подписчиков, причём безвозвратно. Теперь запись отменяется."""
    monkeypatch.setattr(subs_mod, "_push_to_github", _REAL_PUSH)
    monkeypatch.setenv("GITHUB_PAT", "t")
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "new-key")
    monkeypatch.delenv("STATE_FORCE_OVERWRITE", raising=False)

    alerts = []
    monkeypatch.setattr(subs_mod, "_alert_admin", lambda text: alerts.append(text))

    # На сервере лежит валидный конверт, зашифрованный ДРУГИМ ключом.
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "old-key")
    foreign, _ = subs_mod._encode_payload({"777": {"rooms": 1}}, True)
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "new-key")

    puts = []
    monkeypatch.setattr(subs_mod.httpx, "get", lambda *a, **k: _resp_factory(foreign))
    monkeypatch.setattr(
        subs_mod.httpx, "put",
        lambda url, headers=None, json=None, timeout=None: puts.append(json) or _resp_factory("{}"),
    )

    save_json_state(tmp_path / "subscriptions.json", {"111": {"rooms": 2}}, "msg")

    assert puts == [], "нельзя писать поверх того, что не смогли прочитать"
    assert alerts and "не читается" in alerts[0]


def test_force_overwrite_flag_allows_writing_over_unreadable_remote(monkeypatch, tmp_path):
    """Аварийный выключатель для случая, когда ключ потерян навсегда."""
    monkeypatch.setattr(subs_mod, "_push_to_github", _REAL_PUSH)
    monkeypatch.setenv("GITHUB_PAT", "t")
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "new-key")
    monkeypatch.setenv("STATE_FORCE_OVERWRITE", "1")
    monkeypatch.setattr(subs_mod, "_alert_admin", lambda text: None)

    puts = []
    monkeypatch.setattr(subs_mod.httpx, "get", lambda *a, **k: _resp_factory("не-json-мусор"))
    monkeypatch.setattr(
        subs_mod.httpx, "put",
        lambda url, headers=None, json=None, timeout=None: puts.append(json) or _resp_factory("{}"),
    )

    save_json_state(tmp_path / "subscriptions.json", {"111": {"rooms": 2}}, "msg")
    assert len(puts) == 1


def test_missing_remote_file_still_writes(monkeypatch, tmp_path):
    """Пустой ответ (файла на сервере ещё нет) — это не «не прочитали», это
    первая запись. Fail-closed не должен блокировать нормальный первый push."""
    monkeypatch.setattr(subs_mod, "_push_to_github", _REAL_PUSH)
    monkeypatch.setenv("GITHUB_PAT", "t")
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(subs_mod, "_alert_admin", lambda text: None)

    puts = []
    monkeypatch.setattr(subs_mod.httpx, "get", lambda *a, **k: _resp_factory(""))
    monkeypatch.setattr(
        subs_mod.httpx, "put",
        lambda url, headers=None, json=None, timeout=None: puts.append(json) or _resp_factory(""),
    )

    save_json_state(tmp_path / "subscriptions.json", {"111": {"rooms": 2}}, "msg", encrypt=False)
    assert len(puts) == 1
