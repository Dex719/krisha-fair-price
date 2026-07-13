"""Шифрование state-файлов (subscriptions/tracked) в публичном репозитории."""

import json

import pytest

from krisha import subscriptions as subs_mod
from krisha.subscriptions import load_json_state, save_json_state


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
