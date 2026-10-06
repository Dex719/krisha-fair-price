"""STATE_ENCRYPTION_KEY в GitHub Actions: порядок ротации токена из .env.example.

Порядок ротации (.env.example) велит завести секрет STATE_ENCRYPTION_KEY и в
Space, и в Actions. Ревью PR #237: в Actions секрет ни на что не влиял — ни один
шаг не прокидывал его в env, и после смены TELEGRAM_BOT_TOKEN шаги рассылки
вывели бы ключ из нового токена, не расшифровали состояние (fail-closed в
subscriptions.py) и остановили алерты со слежкой. Тесты держат это закрытым.
"""

import re
from pathlib import Path

import pytest
import yaml

from krisha import subscriptions

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
KEY_EXPR = "${{ secrets.STATE_ENCRYPTION_KEY }}"

# (workflow, префикс имени шага): шаги, что читают зашифрованное состояние
STATE_READING_STEPS = [
    ("rescrape.yml", "Alerts"),
    ("rescrape-rent.yml", "Track alerts"),
]
# что в `run:` значит «читает зашифрованное состояние»: pull состояния или рассылка
# (send_alerts пишет channel_posted/alerted_ids и солит хэши chat_id для отчёта)
STATE_READERS = re.compile(r"krisha\.subscriptions\s+--pull|send_alerts\.py")


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(wf: dict):
    for job in wf["jobs"].values():
        yield job, job["steps"]


@pytest.mark.parametrize(("workflow", "prefix"), STATE_READING_STEPS)
def test_state_reading_step_passes_the_encryption_key(workflow, prefix):
    steps = [s for _, ss in _steps(_load(workflow)) for s in ss]
    step = next(s for s in steps if str(s.get("name", "")).startswith(prefix))
    env = step["env"]

    assert env.get("STATE_ENCRYPTION_KEY") == KEY_EXPR
    # токен остаётся: без секрета ключ выводится из него, как раньше
    assert env.get("TELEGRAM_BOT_TOKEN") == "${{ secrets.TELEGRAM_BOT_TOKEN }}"
    assert "krisha.subscriptions --pull" in step["run"]


def test_every_step_reading_state_gets_the_key():
    """Новый шаг, который читает состояние, не должен забыть ключ (иначе секрет
    для ротации снова станет декоративным): проверяем ВСЕ workflow, а не два."""
    seen = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = _load(path.name)
        for job, steps in _steps(wf):
            for step in steps:
                if not STATE_READERS.search(str(step.get("run", ""))):
                    continue
                env = {**(wf.get("env") or {}), **(job.get("env") or {}), **(step.get("env") or {})}
                assert env.get("STATE_ENCRYPTION_KEY") == KEY_EXPR, (
                    f"{path.name}: шаг «{step.get('name')}» читает состояние без STATE_ENCRYPTION_KEY"
                )
                seen.append((path.name, step["name"]))
    # страховка от пустого прохода: оба известных шага действительно найдены
    names = {w for w, _ in seen}
    assert {"rescrape.yml", "rescrape-rent.yml"} <= names


def test_empty_secret_keeps_the_token_derived_key(monkeypatch):
    """Не заведённый секрет Actions приходит ПУСТОЙ строкой: ключ обязан остаться
    выведенным из токена, иначе деплой этого шага сломал бы текущее состояние."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:current-token")
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    sealed = subscriptions._fernet().encrypt(b"state")

    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "")

    assert subscriptions._fernet().decrypt(sealed) == b"state"


def test_rotation_order_keeps_state_readable(monkeypatch):
    """Шаги из .env.example: секрет = ТЕКУЩИЙ токен, потом токен меняется."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:old-token")
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    sealed = subscriptions._fernet().encrypt(b"state")

    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "123:old-token")  # шаг 1
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "456:new-token")  # шаг 2

    assert subscriptions._fernet().decrypt(sealed) == b"state"


def test_new_token_without_the_key_makes_state_unreadable(monkeypatch):
    """Обратный порядок (в Actions без секрета) — то, от чего защищает правка."""
    from cryptography.fernet import InvalidToken

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:old-token")
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    sealed = subscriptions._fernet().encrypt(b"state")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "456:new-token")

    with pytest.raises(InvalidToken):
        subscriptions._fernet().decrypt(sealed)
