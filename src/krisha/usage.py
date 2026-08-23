"""Статистика использования сайта и бота → еженедельный отчёт админу.

События (визит сайта, оценка, сообщение боту) копятся в
`data/usage_stats.json` и коммитятся в GitHub тем же механизмом, что
подписки (см. subscriptions.save_json_state), но не чаще раза в
FLUSH_INTERVAL — иначе каждый визит порождал бы коммит. При рестарте
события с момента последнего флаша теряются — для грубой статистики
это приемлемо.

Приватность: репозиторий публичный, поэтому id пользователей не храним —
только короткий хэш для подсчёта уникальных.

Отчёт шлётся из GitHub Actions (send_alerts.py) по понедельникам
в TG_ADMIN_CHAT_ID.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from krisha.config import DATA_DIR

logger = logging.getLogger(__name__)

USAGE_PATH = DATA_DIR / "usage_stats.json"
ALMATY_TZ = timezone(timedelta(hours=5))
FLUSH_INTERVAL = timedelta(minutes=30)
KEEP_DAYS = 60  # старые дни вычищаем, чтобы файл не рос бесконечно

KINDS = ("site", "predict", "bot")
_LABELS = {"site": "визитов сайта", "predict": "оценок квартир", "bot": "сообщений боту"}
_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# Кэш состояния между запросами (в рамках одного процесса).
_state: dict | None = None
_last_flush: datetime | None = None
# Сериализует _record: хендлеры FastAPI живут в тредпуле (см. комментарий там).
_lock = threading.Lock()


def _usage_salt() -> str:
    """Секрет для соли хэша chat_id — тот же источник, что и шифрование
    state-файлов (см. subscriptions.py): STATE_ENCRYPTION_KEY, иначе
    TELEGRAM_BOT_TOKEN. Без обоих (голая локальная разработка без бота)
    используем фиксированную заглушку — в этом режиме usage_stats.json с
    реальными chat_id никогда не публикуется, деанонимизация неактуальна.
    """
    return (
        os.environ.get("STATE_ENCRYPTION_KEY")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or "krisha-usage-local-dev"
    )


def _hash_user(user_id: int | str) -> str:
    # issue #116: без соли sha256(chat_id)[:10] обратим перебором — chat_id
    # телеграма лежит в известном небольшом диапазоне, а схема хэша видна в
    # этом же публичном репозитории. Соль из секрета (не в репо) делает
    # перебор без ключа непрактичным; агрегатные счётчики остаются читаемыми.
    salt = _usage_salt()
    return hashlib.sha256(f"krisha-usage:{salt}:{user_id}".encode()).hexdigest()[:10]


def load_state() -> dict:
    if USAGE_PATH.exists():
        try:
            return json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("usage_stats.json повреждён — начинаем заново")
    return {"days": {}}


def warm() -> None:
    """Читает состояние с диска заранее (вызывается на старте приложения).

    Иначе первый же посетитель после рестарта делает это под глобальным
    локом: чтение json с диска в момент, когда на сайт валится наплыв.
    """
    global _state
    with _lock:
        if _state is None:
            _state = load_state()


def record_event(kind: str, user_id: int | str | None = None) -> None:
    """Регистрирует событие. Никогда не бросает исключений."""
    try:
        _record(kind, user_id, datetime.now(ALMATY_TZ))
    except Exception:  # noqa: BLE001 — статистика не должна ломать запросы
        logger.exception("Не удалось записать событие статистики")


def _record(kind: str, user_id: int | str | None, now: datetime) -> None:
    global _state, _last_flush
    if kind not in KINDS:
        raise ValueError(f"Неизвестный тип события: {kind}")
    # FastAPI гоняет sync-хендлеры в тредпуле, так что _record зовётся из
    # нескольких потоков разом. Без блокировки: (а) два потока проходили
    # проверку интервала до того, как хоть один выставит _last_flush, и
    # уходили в _flush одновременно — два конкурентных PUT в GitHub по
    # одному и тому же blob sha; (б) _flush сериализовал словарь, который
    # в этот же момент мутировал соседний поток.
    with _lock:
        if _state is None:
            _state = load_state()
        day = _state["days"].setdefault(
            now.strftime("%Y-%m-%d"),
            {"site": 0, "predict": 0, "bot": 0, "bot_users": [], "hours": {}},
        )
        day[kind] = day.get(kind, 0) + 1
        hour = str(now.hour)
        day["hours"][hour] = day["hours"].get(hour, 0) + 1
        if user_id is not None:
            h = _hash_user(user_id)
            if h not in day["bot_users"]:
                day["bot_users"].append(h)
        due = _last_flush is None or now - _last_flush >= FLUSH_INTERVAL
        if not due:
            return
        # Помечаем флаш выполненным и снимаем консистентный снапшот ДО выхода
        # из-под блокировки: сам флаш ходит по сети (GitHub), держать на нём
        # лок нельзя — иначе все запросы выстроятся в очередь за api.github.com.
        _prune(_state, now)
        _last_flush = now
        snapshot = copy.deepcopy(_state)
    _flush_async(snapshot)


def _flush_async(snapshot: dict) -> None:
    """Флаш в отдельном потоке — запрос пользователя его не ждёт.

    Раньше раз в полчаса одному невезучему посетителю сайта доставался ответ
    на секунды дольше: его поток писал файл и делал PUT в api.github.com
    (таймаут 15 с на каждый вызов). Под наплывом это же занимало поток
    тредпула, который в этот момент нужен под запросы. Поток демонский:
    выключение процесса он не задерживает.
    """
    if os.environ.get("USAGE_FLUSH_SYNC") == "1":  # тесты и CLI
        _flush(snapshot)
        return
    threading.Thread(target=_flush, args=(snapshot,), name="usage-flush", daemon=True).start()


def _prune(state: dict, now: datetime) -> None:
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    state["days"] = {d: v for d, v in state["days"].items() if d >= cutoff}


def _flush(state: dict) -> None:
    from krisha.subscriptions import save_json_state

    # encrypt=False: id уже захэшированы, агрегаты полезно видеть в репо глазами
    save_json_state(USAGE_PATH, merge_states(load_state(), state), "data: статистика использования", encrypt=False)


def merge_states(base: dict, incoming: dict) -> dict:
    """Слияние двух снимков статистики: победитель — больший счётчик.

    Нужно, когда сайт крутится в НЕСКОЛЬКИХ воркерах uvicorn (см.
    WEB_CONCURRENCY в Dockerfile): у каждого процесса свои счётчики в памяти,
    и тот, кто флашится вторым, раньше просто затирал файл соседа — половина
    визитов пропадала. Каждый процесс считает свои события с общего значения,
    прочитанного из файла при старте, поэтому максимум по каждому счётчику —
    это «файл + вклад самого активного воркера»: не идеальная сумма, но без
    потери целого воркера и без двойного счёта. Уникальные пользователи бота
    объединяются множеством — там потерь нет вовсе.
    """
    merged: dict = {"days": {}}
    days = set(base.get("days", {})) | set(incoming.get("days", {}))
    for day in days:
        a = base.get("days", {}).get(day) or {}
        b = incoming.get("days", {}).get(day) or {}
        row: dict = {}
        for kind in KINDS:
            row[kind] = max(int(a.get(kind, 0) or 0), int(b.get(kind, 0) or 0))
        users = list(dict.fromkeys(list(a.get("bot_users") or []) + list(b.get("bot_users") or [])))
        row["bot_users"] = users
        hours: dict[str, int] = {}
        for src in (a.get("hours") or {}, b.get("hours") or {}):
            for hour, count in src.items():
                hours[hour] = max(hours.get(hour, 0), int(count or 0))
        row["hours"] = hours
        merged["days"][day] = row
    return merged


def weekly_report(state: dict | None = None, now: datetime | None = None) -> str | None:
    """HTML-отчёт за последние 7 дней или None, если событий не было."""
    state = state if state is not None else load_state()
    now = now or datetime.now(ALMATY_TZ)
    week = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    days = {d: state["days"][d] for d in week if d in state.get("days", {})}
    if not days:
        return None

    totals = {k: sum(v.get(k, 0) for v in days.values()) for k in KINDS}
    uniq_bot = len({u for v in days.values() for u in v.get("bot_users", [])})
    hours: dict[str, int] = {}
    for v in days.values():
        for h, n in v.get("hours", {}).items():
            hours[h] = hours.get(h, 0) + n
    top_hours = sorted(hours.items(), key=lambda x: -x[1])[:3]
    by_weekday = sorted(
        days.items(),
        key=lambda kv: -(kv[1].get("site", 0) + kv[1].get("predict", 0) + kv[1].get("bot", 0)),
    )
    busiest_day, busiest = by_weekday[0]
    busiest_n = busiest.get("site", 0) + busiest.get("predict", 0) + busiest.get("bot", 0)
    wd = _WEEKDAYS[datetime.strptime(busiest_day, "%Y-%m-%d").weekday()]

    lines = [
        "📊 <b>Статистика за неделю</b> (сайт + бот)",
        "",
        *(f"{_LABELS[k]}: <b>{totals[k]}</b>" for k in KINDS if totals[k]),
    ]
    if uniq_bot:
        lines.append(f"уникальных пользователей бота: <b>{uniq_bot}</b>")
    if top_hours:
        lines.append(
            "Пиковые часы (Алматы): "
            + " · ".join(f"{h}:00 ({n})" for h, n in top_hours)
        )
    lines.append(f"Самый активный день: {wd} {busiest_day} ({busiest_n} событий)")
    return "\n".join(lines)


def send_weekly_report(dry_run: bool = False) -> bool:
    """Шлёт отчёт в TG_ADMIN_CHAT_ID. False — нечего слать или нет чата."""
    import os

    from krisha.bot import tg_call

    text = weekly_report()
    if not text:
        logger.info("Событий за неделю нет — отчёт не шлём")
        return False
    if dry_run:
        print(text)
        return True
    chat_id = os.environ.get("TG_ADMIN_CHAT_ID")
    if not chat_id:
        logger.info("TG_ADMIN_CHAT_ID не задан — отчёт статистики не отправляем")
        return False
    resp = tg_call("sendMessage", chat_id=int(chat_id), text=text, parse_mode="HTML")
    # ok=false (400 на разметке и т.п.) — это НЕ успешная отправка, см. тот же
    # разбор в monitoring.notify_retrain_report.
    return bool(resp and resp.get("ok"))
