"""Подписки на алерты о выгодных объявлениях.

Хранение: data/subscriptions.json. Файл живёт в git-репозитории — это
единственное бесплатное персистентное хранилище в нашей схеме (диск на
хостинге стирается при каждом рестарте). Поэтому при изменении подписки
приложение коммитит файл через GitHub Contents API в ПРИВАТНЫЙ репозиторий
данных (STATE_REPO, рядом с базой — см. krisha.db_release), а при старте
забирает оттуда всё состояние (pull_state). Токен — KRISHA_DB_TOKEN
(contents:write только на репо данных); без него состояние живёт до
ближайшего рестарта.

Приватность: chat_id подписчиков — PII, поэтому содержимое файла вдобавок
шифруется целиком (Fernet). Ключ выводится из
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
import sys
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
# Состояние — в приватном репозитории данных, не в публичном репо кода: раньше
# рантайм Space коммитил его прямо в main токеном с правом записи в код.
STATE_REPO = os.environ.get("KRISHA_STATE_REPO", "Dex719/krisha-db")
_GH_API = "https://api.github.com"
# Всё, что рантайм и алерты пишут через save_json_state: при старте Space и
# перед алертами в Actions это забирается из STATE_REPO (pull_state).
STATE_FILES = (
    "subscriptions.json",
    "tracked.json",
    "usage_stats.json",
    "prediction_log.json",
    "channel_posted.json",
    "alerted_ids.json",
    "alerted_chats.json",
)
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
        _save(subs, "alerts: обновление подписок", touched_keys={str(chat_id)})


def remove_subscription(chat_id: int) -> bool:
    with STATE_LOCK:
        subs = load_subscriptions()
        if str(chat_id) not in subs:
            return False
        del subs[str(chat_id)]
        # deleted_keys: иначе слияние с удалённой копией вернёт отписавшегося
        _save(subs, "alerts: обновление подписок", deleted_keys={str(chat_id)}, touched_keys=set())
        return True


def move_subscription(old_chat_id: int | str, new_chat_id: int | str) -> bool:
    """Группа стала супергруппой (migrate_to_chat_id): подписка переезжает на новый id."""
    old, new = str(old_chat_id), str(new_chat_id)
    with STATE_LOCK:
        subs = load_subscriptions()
        if old not in subs:
            return False
        subs[new] = subs.pop(old)
        _save(subs, "alerts: обновление подписок", deleted_keys={old}, touched_keys={new})
        return True


def forget_chat(chat_id: int | str) -> None:
    """Чат недоступен навсегда (бот заблокирован, чат удалён): убираем
    подписку и слежку, чтобы рассылка не стучалась туда каждый день."""
    from krisha.tracking import remove_tracked

    remove_subscription(int(chat_id))
    remove_tracked(int(chat_id), None)


def migrate_chat(old_chat_id: int | str, new_chat_id: int | str) -> None:
    """Перенос подписки и слежки на новый id чата (группа → супергруппа)."""
    from krisha.tracking import move_tracked

    move_subscription(old_chat_id, new_chat_id)
    move_tracked(old_chat_id, new_chat_id)


def _save(
    subs: dict[str, Any],
    message: str,
    deleted_keys: set[str] | None = None,
    touched_keys: set[str] | None = None,
) -> None:
    save_json_state(
        SUBSCRIPTIONS_PATH, subs, message, deleted_keys=deleted_keys, touched_keys=touched_keys,
    )


def save_json_state(
    path,
    data: Any,
    message: str,
    encrypt: bool = True,
    deleted_keys: set[str] | None = None,
    touched_keys: set[str] | None = None,
    update_only: bool = False,
    normalize=None,
) -> Any:
    """Сохраняет JSON-состояние локально и коммитит в GitHub (см. докстринг модуля).

    Возвращает то, что в итоге записано (после слияния с сервером), — по нему
    вызывающий проверяет, что его правка пережила слияние (лимит слежки).

    Общий механизм для subscriptions.json, tracked.json и др.
    encrypt=True шифрует содержимое целиком (файлы с chat_id — PII);
    usage-статистика и опубликованные id пишутся открыто (encrypt=False).

    `deleted_keys` — ключи верхнего уровня, которые вызывающий УДАЛИЛ намеренно
    (отписка, /untrack). Нужны для слияния при конкурентной записи: без них
    «ключа нет у нас, но есть на сервере» неотличимо от «его только что
    добавил другой писатель», см. _push_to_github (issue #111).

    `touched_keys` — единственные ключи, которые вызывающий менял (команда
    бота трогает один чат). Остальное при слиянии берётся с сервера, а не из
    локальной копии: у Space она с момента старта, и ночные правки Actions
    (новые базовые цены, снятые лоты) одна команда /track любого чата
    откатывала у ВСЕХ чатов. None — писатель отвечает за файл целиком.

    Ключ вида "chat:lot" в `touched_keys`/`deleted_keys` — правка одного лота
    внутри чата (tracked.json): слияние идёт на уровне пары, см. _merge_remote.
    `update_only` — тронутые ключи только обновляют то, что есть на сервере,
    и не воскрешают удалённое там (ночная сверка цен в Actions).
    `normalize(merged)` — правило файла поверх слияния (лимит лотов на чат).
    """
    payload, encrypted = _encode_payload(data, encrypt)
    if encrypt and not encrypted:
        logger.warning(
            "%s: нет ключа шифрования (STATE_ENCRYPTION_KEY/TELEGRAM_BOT_TOKEN) — "
            "сохраняю только локально открытым текстом", path.name,
        )
    _write_local(path, payload)
    # PII-состояние без ключа никогда не публикуем в репозиторий.
    if not encrypt or encrypted:
        written = _push_to_github(
            path, payload, message, data, deleted_keys, encrypt, touched_keys,
            update_only=update_only, normalize=normalize,
        )
        if written is not None:
            return written
    return data


def _write_local(path, text: str) -> None:
    """Атомарная запись: tmp + replace защищает от обрезанного JSON при
    остановке процесса во время записи."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _state_token() -> str | None:
    """KRISHA_DB_TOKEN — штатный; GITHUB_PAT/GITHUB_TOKEN — наследие схемы,
    где состояние лежало в репо кода. Чужой токен до приватного репо не
    дотянется, и неудачная запись дойдёт до админа алертом, а не потеряется."""
    return (
        os.environ.get("KRISHA_DB_TOKEN")
        or os.environ.get("GITHUB_PAT")
        or os.environ.get("GITHUB_TOKEN")
    )


def pull_state(names=STATE_FILES) -> tuple[int, int]:
    """Забирает state-файлы из STATE_REPO в DATA_DIR: (скачано, ошибок).

    Образ Space и checkout в Actions состояния не содержат — без этого шага
    бот после рестарта не видел бы подписчиков, а алерты повторно постили бы
    в канал уже опубликованные лоты. Файл кладётся как есть (зашифрованная
    обёртка остаётся обёрткой). 404 — файла ещё нет, это не ошибка.
    """
    token = _state_token()
    if not token:
        logger.warning("KRISHA_DB_TOKEN не задан — состояние из %s не загружено", STATE_REPO)
        return 0, 0
    headers = {
        "Authorization": f"Bearer {token}",
        # raw — файл целиком, без base64-обёртки и её потолка в 1 МБ
        "Accept": "application/vnd.github.raw+json",
    }
    pulled = failed = 0
    with STATE_LOCK:
        for name in names:
            url = f"{_GH_API}/repos/{STATE_REPO}/contents/data/{name}"
            try:
                resp = httpx.get(url, headers=headers, timeout=15.0)
            except httpx.HTTPError as exc:
                logger.warning("Состояние %s не загружено: %s", name, exc)
                failed += 1
                continue
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                logger.warning("Состояние %s не загружено: HTTP %s", name, resp.status_code)
                failed += 1
                continue
            if resp.text.strip() and _decode_payload(resp.text, name) is None:
                # Файл на сервере есть, но не читается (сменился ключ шифрования,
                # битый JSON). Раньше это молча превращалось в «подписчиков нет»:
                # алерты и слежка выключались, и никто об этом не знал. Локальную
                # копию не трогаем, считаем сбоем и говорим админу.
                logger.error("Состояние %s скачано, но не читается — проверь ключ шифрования", name)
                _alert_admin(
                    f"⛔️ data/{name}: состояние не расшифровывается (сменился "
                    f"STATE_ENCRYPTION_KEY или токен бота?). Алерты и слежка по нему не работают."
                )
                failed += 1
                continue
            _write_local(DATA_DIR / name, resp.text)
            pulled += 1
    logger.info("Состояние из %s: загружено %d, ошибок %d", STATE_REPO, pulled, failed)
    return pulled, failed


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


def _merge_remote(
    local: Any,
    remote: Any,
    deleted_keys: set[str] | None,
    touched_keys: set[str] | None = None,
    update_only: bool = False,
    normalize=None,
) -> Any:
    """Сливает удалённое состояние в локальное перед PUT (issue #111).

    Ключ верхнего уровня во всех state-файлах — chat_id, поэтому слияние
    определено однозначно на этом уровне: берём удалённую версию за основу и
    накладываем свои ключи сверху (наши свежее), а затем выкидываем то, что
    мы удалили намеренно. Без `deleted_keys` отписка бы «воскресала» из
    удалённой копии.

    Ключ "chat:lot" — правка одного лота чата (tracked.json). Раньше
    гранулярность была чат целиком, и при двух воркерах uvicorn два быстрых
    «Следить» в одном чате расходились по разным процессам: второй писатель
    клал свою версию чата поверх и терял лот первого (и обходил лимит).
    Теперь накладываем ровно пару (чат, лот), а удалённую пару — убираем.

    С `touched_keys` (см. save_json_state) поверх сервера кладём только их:
    локальная копия остальных чатов может быть устаревшей. `update_only` —
    тронутое только обновляет существующее на сервере (не воскрешает то, что
    там успели удалить). `normalize` — правило файла поверх результата.

    Не словари (или сервер отдал мусор) — сливать нечего, пишем как есть.
    """
    if not isinstance(local, dict) or not isinstance(remote, dict):
        return local
    if touched_keys is None:
        merged = {**remote, **local}
    else:
        merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in remote.items()}
        for key in touched_keys:
            chat, sep, lot = key.partition(":")
            if not sep:
                if key in local and (not update_only or key in remote):
                    merged[key] = local[key]
                continue
            mine = local.get(chat)
            if not isinstance(mine, dict) or lot not in mine:
                continue
            theirs = merged.get(chat)
            if update_only and not (isinstance(theirs, dict) and lot in theirs):
                continue
            if not isinstance(theirs, dict):
                theirs = merged[chat] = {}
            theirs[lot] = mine[lot]
    for key in deleted_keys or ():
        chat, sep, lot = key.partition(":")
        if not sep:
            merged.pop(key, None)
            continue
        theirs = merged.get(chat)
        if isinstance(theirs, dict):
            theirs.pop(lot, None)
            if not theirs:
                merged.pop(chat, None)
    if normalize is not None:
        merged = normalize(merged)
    return merged


def _push_to_github(
    path,
    payload: str,
    message: str,
    data: Any = None,
    deleted_keys: set[str] | None = None,
    encrypt: bool = True,
    touched_keys: set[str] | None = None,
    update_only: bool = False,
    normalize=None,
) -> Any:
    """Коммитит файл состояния в STATE_REPO, чтобы пережить рестарт.

    Возвращает записанные данные (после слияния) или None, если записи в
    GitHub не было (нет токена, ошибка) — тогда в силе локальная копия.

    Токен — см. _state_token (KRISHA_DB_TOKEN и на Space, и в Actions).

    issue #111: у файла ДВА независимых писателя — Space (команды бота) и
    GitHub Actions (алерты, usage). Раньше здесь читался sha и сразу же
    делался PUT, то есть конфликт, который должен был дать 409, превращался
    в успешную перезапись: чужие изменения (например только что оформленная
    подписка) исчезали молча. Теперь удалённое состояние сливается с нашим
    перед записью, а 409/422 от GitHub — это повод перечитать и повторить,
    а не потерять данные.
    """
    token = _state_token()
    if not token:
        logger.warning("KRISHA_DB_TOKEN не задан — %s сохранён только локально", path.name)
        return None
    rel = f"data/{path.name}"
    url = f"{_GH_API}/repos/{STATE_REPO}/contents/{rel}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    for attempt in range(1, PUSH_MAX_ATTEMPTS + 1):
        try:
            resp = httpx.get(url, headers=headers, timeout=15.0)
            sha = resp.json().get("sha") if resp.status_code == 200 else None
            body_payload = payload
            written = data
            if sha and data is not None:
                raw_remote = _remote_text(resp, url, token)
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
                        return None
                    logger.warning(
                        "%s: %s=1 — перезаписываю нечитаемое удалённое состояние",
                        rel, FORCE_OVERWRITE_ENV,
                    )
                elif remote is not None:
                    merged = _merge_remote(
                        data, remote, deleted_keys, touched_keys,
                        update_only=update_only, normalize=normalize,
                    )
                    written = merged
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
                if body_payload != payload:
                    # локально — то же, что ушло на сервер, а не устаревшая копия
                    _write_local(path, body_payload)
                return written
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
            return None
        except httpx.HTTPError as exc:
            logger.warning("GitHub push %s не удался: %s", rel, exc)
            _alert_admin(f"⚠️ {rel}: не сохранено в GitHub — {type(exc).__name__}")
            return None
    # Ретраи исчерпаны: локальная копия уже записана, удалённая осталась чужой.
    logger.error("GitHub push %s: конфликт не разрешён за %d попыток", rel, PUSH_MAX_ATTEMPTS)
    _alert_admin(f"⚠️ {rel}: конфликт записи не разрешён за {PUSH_MAX_ATTEMPTS} попытки")
    return None


def _remote_text(resp, url: str | None = None, token: str | None = None) -> str:
    """Содержимое файла из ответа GitHub Contents API.

    Для файлов больше 1 МБ Contents API отдаёт `content: ""` и
    `encoding: "none"` — раньше это читалось как пустой файл: слияние
    пропускалось, и PUT затирал всё, что было на сервере (prediction_log
    давно перерос мегабайт). Тогда дочитываем файл raw-запросом, как pull_state.
    sha берётся из первого ответа: если файл успел измениться между двумя
    GET, PUT со старым sha получит 409 и пойдёт на новую попытку.
    """
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        return ""
    content = body.get("content") or ""
    too_big = body.get("encoding") == "none" or (not content and (body.get("size") or 0) > 0)
    if not too_big:
        try:
            return base64.b64decode(content).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
    if not url or not token:
        return ""
    raw = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"},
        timeout=30.0,
    )
    if raw.status_code != 200:
        # Не прочитали — пусть сработает fail-closed выше, а не перезапись.
        raise httpx.HTTPError(f"raw GET HTTP {raw.status_code}")
    return raw.text


def main(argv: list[str] | None = None) -> int:
    """CLI для Actions: ``python -m krisha.subscriptions --pull``."""
    import argparse

    parser = argparse.ArgumentParser(description="Состояние бота в репозитории данных")
    parser.add_argument("--pull", action="store_true", help="скачать state-файлы в data/")
    args = parser.parse_args(argv)
    if not args.pull:
        parser.print_help()
        return 2
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not _state_token():
        print("KRISHA_DB_TOKEN не задан", file=sys.stderr)
        return 1
    _, failed = pull_state()
    # Неполное состояние хуже отсутствующего прохода: без channel_posted
    # алерты заново запостят в канал уже опубликованное.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
