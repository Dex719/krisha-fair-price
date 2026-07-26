"""Подписки на алерты о выгодных объявлениях.

Хранение: data/subscriptions.json. Файл живёт в git-репозитории — это
единственное бесплатное персистентное хранилище в нашей схеме (диск на
хостинге стирается при каждом деплое). Поэтому при изменении подписки
приложение коммитит файл в GitHub через Contents API (нужен env GITHUB_PAT
с правом contents:write; без него подписки живут до ближайшего деплоя).

Приватность: репозиторий публичный, а chat_id подписчиков — PII, поэтому
содержимое файла шифруется целиком (Fernet). Ключ выводится из
STATE_ENCRYPTION_KEY, а если её нет — из TELEGRAM_BOT_TOKEN (он и так есть
и на сервере, и в GitHub Actions, где шлются алерты). Без ключа сохраняем
как раньше открытым JSON — актуально только для локальной разработки.
ВНИМАНИЕ: смена токена бота без STATE_ENCRYPTION_KEY делает старые данные
нечитаемыми — подписки обнулятся (залогируем и продолжим).

Формат (расшифрованный): {"<chat_id>": {"rooms": 2|null,
"max_price": 45000000|null, "district": "Bostandykskiy_r-n"|null, "since": iso}}
На диске: {"_encrypted": "<fernet-token>"} либо legacy plaintext-JSON
(читается и перешифровывается при первом же сохранении).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from krisha.config import DATA_DIR
from krisha.stats import DISTRICT_RU

logger = logging.getLogger(__name__)

SUBSCRIPTIONS_PATH = DATA_DIR / "subscriptions.json"
# Сериализует цикл «прочитал файл → поменял → записал» для state-файлов.
# Апдейты Telegram обрабатываются в BackgroundTasks поверх тредпула, так что
# два /alerts_on или /track от разных пользователей запросто идут параллельно:
# оба читают одну и ту же версию файла, и тот, кто записал вторым, затирает
# правку первого. RLock — чтобы вложенные вызовы (save внутри mutate) не
# заклинивали сами себя.
STATE_LOCK = threading.RLock()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Dex719/krisha-fair-price")
_GH_API = "https://api.github.com"
STATE_KEY_ENV = "STATE_ENCRYPTION_KEY"


def _fernet():
    """Fernet для state-файлов или None, если ключа нет (локальная разработка).

    Ключ — SHA-256 от секрета с доменным префиксом: валидный 32-байтовый
    urlsafe-base64, как требует Fernet.
    """
    secret = os.environ.get(STATE_KEY_ENV) or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not secret:
        return None
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(f"kfp-state:{secret}".encode()).digest())
    return Fernet(key)


def _decode_payload(text: str, name: str = "state"):
    """Сырой текст state-файла → данные: расшифровывает обёртку
    {"_encrypted": ...} или отдаёт legacy plaintext-JSON. Битый/нет ключа → None."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and "_encrypted" in data:
        f = _fernet()
        if f is None:
            logger.warning("%s зашифрован, а ключа нет (STATE_ENCRYPTION_KEY/токен)", name)
            return None
        from cryptography.fernet import InvalidToken

        try:
            return json.loads(f.decrypt(str(data["_encrypted"]).encode()))
        except (InvalidToken, json.JSONDecodeError):
            logger.warning("Не удалось расшифровать %s — сменился ключ? Начинаем заново", name)
            return None
    return data


def load_json_state(path):
    """Читает state-файл: расшифровывает обёртку {"_encrypted": ...} или
    отдаёт legacy plaintext-JSON. Нет файла/битый/нет ключа → None."""
    try:
        # encoding обязателен: пишем мы всегда utf-8 (см. save_json_state ниже),
        # а read_text() без него берёт локаль ОС — на Windows это cp1251, и
        # кириллица в tracked.json/subscriptions.json читается «РљРІР°СЂС‚РёСЂР°».
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    return _decode_payload(text, path.name)


def _encode_payload(data: Any, encrypt: bool) -> tuple[str, bool]:
    """Данные → (текст для записи, зашифровано ли)."""
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    if not encrypt:
        return payload, False
    f = _fernet()
    if f is None:
        return payload, False
    return json.dumps({"_encrypted": f.encrypt(payload.encode()).decode()}, indent=2), True

# «2к», «2-к», «2комн» и просто «2» → комнаты; «45млн», «до 45» → бюджет
_ROOMS_RE = re.compile(r"^([1-6])(?:-?к(?:омн\w*)?)?$", re.IGNORECASE)
_PRICE_RE = re.compile(r"^(\d{1,4}(?:[.,]\d+)?)(?:млн)?$", re.IGNORECASE)


def load_subscriptions() -> dict[str, dict[str, Any]]:
    data = load_json_state(SUBSCRIPTIONS_PATH)
    return data if isinstance(data, dict) else {}


def parse_filters(text: str) -> dict[str, Any]:
    """Разбирает хвост команды /alerts_on: комнаты, бюджет (млн ₸), район.

    Числа 1–6 (или «2к») — комнаты; числа ≥7 (или «45млн») — максимум цены
    в млн ₸; слово — район (по подстроке русского названия).
    """
    flt: dict[str, Any] = {"rooms": None, "max_price": None, "district": None}
    for token in re.split(r"[\s,]+", text.strip()):
        if not token or token.lower() in ("до",):
            continue
        m = _ROOMS_RE.match(token)
        if m and flt["rooms"] is None:
            flt["rooms"] = int(m.group(1))
            continue
        m = _PRICE_RE.match(token)
        if m:
            value = float(m.group(1).replace(",", "."))
            if value >= 7:  # млн ₸; меньшие числа — это были бы комнаты
                flt["max_price"] = int(value * 1_000_000)
                continue
        low = token.lower().strip(".")
        for slug, ru in DISTRICT_RU.items():
            if low in ru.lower() or ru.lower().startswith(low):
                flt["district"] = slug
                break
    return flt


def describe_filters(flt: dict[str, Any]) -> str:
    parts = []
    if flt.get("rooms"):
        parts.append(f"{flt['rooms']}-комн")
    if flt.get("max_price"):
        parts.append(f"до {flt['max_price'] / 1_000_000:g} млн ₸")
    if flt.get("district"):
        parts.append(DISTRICT_RU.get(flt["district"], flt["district"]))
    return ", ".join(parts) if parts else "без фильтров (все выгодные)"


def set_subscription(chat_id: int, flt: dict[str, Any]) -> None:
    with STATE_LOCK:
        subs = load_subscriptions()
        subs[str(chat_id)] = {**flt, "since": datetime.now(timezone.utc).isoformat()}
        # Без chat_id в message: репозиторий публичный, история коммитов — тоже
        _save(subs, "alerts: обновление подписок")


def remove_subscription(chat_id: int) -> bool:
    with STATE_LOCK:
        subs = load_subscriptions()
        if str(chat_id) not in subs:
            return False
        del subs[str(chat_id)]
        # deleted_keys: иначе слияние с удалённой копией вернёт отписавшегося
        _save(subs, "alerts: обновление подписок", deleted_keys={str(chat_id)})
        return True


def _save(subs: dict[str, Any], message: str, deleted_keys: set[str] | None = None) -> None:
    save_json_state(SUBSCRIPTIONS_PATH, subs, message, deleted_keys=deleted_keys)


def save_json_state(
    path,
    data: Any,
    message: str,
    encrypt: bool = True,
    deleted_keys: set[str] | None = None,
) -> None:
    """Сохраняет JSON-состояние локально и коммитит в GitHub (см. докстринг модуля).

    Общий механизм для subscriptions.json, tracked.json и др.
    encrypt=True шифрует содержимое целиком (файлы с chat_id — PII в публичном
    репо); usage-статистика и опубликованные id пишутся открыто (encrypt=False).

    `deleted_keys` — ключи верхнего уровня, которые вызывающий УДАЛИЛ намеренно
    (отписка, /untrack). Нужны для слияния при конкурентной записи: без них
    «ключа нет у нас, но есть на сервере» неотличимо от «его только что
    добавил другой писатель», см. _push_to_github (issue #111).
    """
    payload, encrypted = _encode_payload(data, encrypt)
    if encrypt and not encrypted:
        logger.warning(
            "%s: нет ключа шифрования (STATE_ENCRYPTION_KEY/TELEGRAM_BOT_TOKEN) — "
            "сохраняю только локально открытым текстом", path.name,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # tmp + replace защищает от обрезанного JSON при остановке процесса во время записи.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    # PII-состояние без ключа никогда не публикуем в репозиторий.
    if not encrypt or encrypted:
        _push_to_github(path, payload, message, data, deleted_keys, encrypt)


PUSH_MAX_ATTEMPTS = 3
# Аварийный выключатель fail-closed: ставится оператором, когда ключ шифрования
# потерян НАВСЕГДА и удалённое состояние решено осознанно затереть.
FORCE_OVERWRITE_ENV = "STATE_FORCE_OVERWRITE"


def _force_overwrite() -> bool:
    return os.environ.get(FORCE_OVERWRITE_ENV, "0") == "1"


def _alert_admin(text: str) -> None:
    """Сообщение админу о проблеме с сохранением состояния.

    До этого единственным следом неудачной записи был `logger.error` в
    контейнере, который никто не читает: протухший по сроку токен или
    неразрешённый конфликт означали тихую потерю подписок. Fail-soft —
    сам алерт не должен ронять команду бота.
    """
    chat_id = os.environ.get("TG_ADMIN_CHAT_ID")
    if not chat_id:
        return
    try:
        from krisha.bot import tg_call

        tg_call("sendMessage", chat_id=int(chat_id), text=text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось отправить алерт о состоянии: %s", exc)


def _merge_remote(local: Any, remote: Any, deleted_keys: set[str] | None) -> Any:
    """Сливает удалённое состояние в локальное перед PUT (issue #111).

    Ключ верхнего уровня во всех state-файлах — chat_id, поэтому слияние
    определено однозначно на этом уровне: берём удалённую версию за основу и
    накладываем свои ключи сверху (наши свежее), а затем выкидываем то, что
    мы удалили намеренно. Без `deleted_keys` отписка бы «воскресала» из
    удалённой копии.

    Гранулярность — именно чат целиком: если два писателя одновременно
    правили РАЗНЫЕ лоты ОДНОГО чата в tracked.json, победит наша версия
    чата. Это осознанный компромисс: одновременная правка одного чата с
    двух сторон практически не встречается, а полноценный merge требует
    хранить дельту, а не итоговое состояние (см. issue #111 про переход к
    единственному писателю).

    Не словари (или сервер отдал мусор) — сливать нечего, пишем как есть.
    """
    if not isinstance(local, dict) or not isinstance(remote, dict):
        return local
    merged = {**remote, **local}
    for key in deleted_keys or ():
        merged.pop(key, None)
    return merged


def _push_to_github(
    path,
    payload: str,
    message: str,
    data: Any = None,
    deleted_keys: set[str] | None = None,
    encrypt: bool = True,
) -> None:
    """Коммитит файл состояния в GitHub, чтобы пережить редеплой.

    Токен: GITHUB_PAT (сервер) или GITHUB_TOKEN (GitHub Actions,
    у workflow есть contents:write).

    issue #111: у файла ДВА независимых писателя — Space (команды бота) и
    GitHub Actions (алерты, usage). Раньше здесь читался sha и сразу же
    делался PUT, то есть конфликт, который должен был дать 409, превращался
    в успешную перезапись: чужие изменения (например только что оформленная
    подписка) исчезали молча. Теперь удалённое состояние сливается с нашим
    перед записью, а 409/422 от GitHub — это повод перечитать и повторить,
    а не потерять данные.
    """
    token = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN")
    if not token:
        logger.warning("GITHUB_PAT/GITHUB_TOKEN не задан — %s сохранён только локально", path.name)
        return
    rel = f"data/{path.name}"
    url = f"{_GH_API}/repos/{GITHUB_REPO}/contents/{rel}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    for attempt in range(1, PUSH_MAX_ATTEMPTS + 1):
        try:
            resp = httpx.get(url, headers=headers, timeout=15.0)
            sha = resp.json().get("sha") if resp.status_code == 200 else None
            body_payload = payload
            if sha and data is not None:
                raw_remote = _remote_text(resp)
                remote = _decode_payload(raw_remote, path.name)
                if remote is None and raw_remote.strip():
                    # FAIL-CLOSED (issue #150). Файл на сервере ЕСТЬ, но мы его
                    # не прочитали: сменился STATE_ENCRYPTION_KEY, потерялся
                    # секрет при деплое, битый JSON. Раньше здесь молча уходил
                    # PUT с нашим payload и чужим sha — свежеподнятый Space с
                    # пустым локальным состоянием одной командой /alerts_on
                    # стирал всех подписчиков, причём безвозвратно: перезапись
                    # уничтожала и то, что можно было бы спасти, вернув ключ.
                    # Не пишем поверх того, что не смогли прочитать.
                    if not _force_overwrite():
                        logger.error(
                            "%s: удалённое состояние не читается — запись ОТМЕНЕНА "
                            "(проверь ключ шифрования; принудительно: %s=1)",
                            rel, FORCE_OVERWRITE_ENV,
                        )
                        _alert_admin(
                            f"⛔️ {rel}: удалённое состояние не читается, запись отменена.\n"
                            f"Похоже, сменился ключ шифрования. Данные на сервере целы.\n"
                            f"Перезаписать намеренно: переменная {FORCE_OVERWRITE_ENV}=1"
                        )
                        return
                    logger.warning(
                        "%s: %s=1 — перезаписываю нечитаемое удалённое состояние",
                        rel, FORCE_OVERWRITE_ENV,
                    )
                elif remote is not None:
                    merged = _merge_remote(data, remote, deleted_keys)
                    if merged != data:
                        logger.info(
                            "%s: слил конкурентные изменения с сервера (+%d ключей)",
                            rel,
                            len(merged) - len(data) if isinstance(merged, dict) else 0,
                        )
                        body_payload, _ = _encode_payload(merged, encrypt)
            body = {
                "message": message,
                "content": base64.b64encode(body_payload.encode()).decode(),
                **({"sha": sha} if sha else {}),
            }
            put = httpx.put(url, headers=headers, json=body, timeout=15.0)
            if put.status_code in (200, 201):
                return
            if put.status_code in (409, 422) and attempt < PUSH_MAX_ATTEMPTS:
                # Кто-то записал файл между нашими GET и PUT — перечитываем
                # sha и сливаемся заново, а не затираем его правку.
                logger.warning(
                    "GitHub push %s: конфликт %s, попытка %d/%d",
                    rel, put.status_code, attempt, PUSH_MAX_ATTEMPTS,
                )
                time.sleep(0.5 * attempt)
                continue
            logger.error("GitHub push %s: %s %s", rel, put.status_code, put.text[:200])
            _alert_admin(f"⚠️ {rel}: не сохранено в GitHub — HTTP {put.status_code}")
            return
        except httpx.HTTPError as exc:
            logger.warning("GitHub push %s не удался: %s", rel, exc)
            _alert_admin(f"⚠️ {rel}: не сохранено в GitHub — {type(exc).__name__}")
            return
    # Ретраи исчерпаны: локальная копия уже записана, удалённая осталась чужой.
    logger.error("GitHub push %s: конфликт не разрешён за %d попыток", rel, PUSH_MAX_ATTEMPTS)
    _alert_admin(f"⚠️ {rel}: конфликт записи не разрешён за {PUSH_MAX_ATTEMPTS} попытки")


def _remote_text(resp) -> str:
    """Содержимое файла из ответа GitHub Contents API (base64)."""
    try:
        return base64.b64decode(resp.json().get("content") or "").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""
