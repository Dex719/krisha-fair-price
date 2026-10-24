"""Ежедневные алерты: новые объявления с вердиктом «выгодно».

Запускается из GitHub Actions после рескрейпа (scripts/send_alerts.py):
берём объявления, впервые увиденные за последние ALERT_WINDOW_HOURS,
прогоняем через модель и рассылаем подписчикам те, что дешевле нижней
границы интервала справедливой цены (GOOD_DEAL) и проходят фильтры.
"""

from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krisha.config import DB_PATH
from krisha.db import get_conn
from krisha.numfmt import dec, pct
from krisha.scam import FRESH_DAYS
from krisha.stats import DISTRICT_RU

logger = logging.getLogger(__name__)

ALERT_WINDOW_HOURS = 26  # запас к суточному крону
MAX_DEALS_PER_CHAT = 5

# issue #127 сделал sighting и докачку детали разными очередями: id попадает
# в базу сразу, а детальная страница — когда до неё дойдёт лимитированная
# очередь, порой через несколько проходов. Окно только по first_seen из-за
# этого пропускало лот навсегда: пока деталей нет, у строки нет ни area, ни
# нормальной цены (её отсекает `price > 0 AND area > 0`), а когда детали
# приезжают — first_seen уже вне окна. Поэтому смотрим ещё и на scraped_at,
# то есть на момент, когда лот стал пригоден для оценки.
#
# Плата за это — scraped_at обновляет и очередь дообновления устаревших
# деталей (issue #102), так что по одному только окну старый лот попадал бы
# в алерты повторно. От повторов защищает файл уже разосланных id.
ALERTED_PATH = Path(__file__).resolve().parents[2] / "data" / "alerted_ids.json"
ALERTED_KEEP = 2000


def load_alerted(path: Path | None = None) -> list[int]:
    import json

    path = path or ALERTED_PATH
    if not path.exists():
        return []
    try:
        return [int(x) for x in json.loads(path.read_text(encoding="utf-8"))]
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("alerts: битый %s — начинаем с нуля", path)
        return []


def remember_alerted(ids: list[int], path: Path | None = None) -> None:
    """Помечает лоты как разосланные, чтобы не слать их повторно."""
    from krisha.subscriptions import save_json_state

    path = path or ALERTED_PATH
    merged = (load_alerted(path) + [int(i) for i in ids])[-ALERTED_KEEP:]
    # encrypt=False: это id публичных объявлений, PII здесь нет
    save_json_state(path, merged, "state: разосланные в алертах лоты", encrypt=False)


# Кому какой лот уже ушёл: {"<chat_id>": [id, ...]}. Глобальный alerted_ids
# пополняется, только когда лот дошёл до ВСЕХ чатов, которым был выбран;
# до тех пор этот журнал не даёт прислать его повторно тем, кто уже получил,
# и не лишает лота тех, до кого он не дошёл (раньше один общий delivered_ids
# помечал лот разосланным после первой же удачной доставки любому чату).
# chat_id — PII, поэтому файл шифруется, как подписки.
ALERTED_CHAT_KEEP = 300


def _alerted_chats_path() -> Path:
    # рядом с ALERTED_PATH: тесты, подменяющие его, заодно изолируют и журнал
    return ALERTED_PATH.with_name("alerted_chats.json")


def load_alerted_chats() -> dict[str, list[int]]:
    from krisha.subscriptions import load_json_state

    data = load_json_state(_alerted_chats_path())
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[int]] = {}
    for chat, ids in data.items():
        try:
            out[str(chat)] = [int(i) for i in ids]
        except (TypeError, ValueError):
            continue
    return out


def match_filters(listing: dict[str, Any], flt: dict[str, Any]) -> bool:
    if flt.get("rooms") and listing.get("rooms") != flt["rooms"]:
        return False
    if flt.get("max_price") and (listing.get("price") or 0) > flt["max_price"]:
        return False
    if flt.get("district") and listing.get("district") != flt["district"]:
        return False
    return True


def new_listings(db_path: Path | str = DB_PATH, hours: int = ALERT_WINDOW_HOURS) -> list[dict]:
    """Активные объявления, впервые увиденные за последние `hours` часов."""
    # issue #115: get_conn (WAL/busy_timeout) вместо голого sqlite3.connect —
    # рассылка идёт рядом по времени с рескрейпом, который активно пишет в базу.
    with get_conn(db_path) as conn:
        # scraped_at двигает и докачка деталей старых лотов (#102): одно окно
        # по нему тащило в «новые» объявления, висящие неделями (84% окна в
        # замере), а «подозрительно дёшево» для них не включалось — scam
        # считает уровень high только у свежих. Лот, чья деталь приехала
        # позже, остаётся кандидатом, если на рынке он не дольше недели.
        rows = conn.execute(
            "SELECT * FROM listings WHERE is_active = 1 AND price > 0 AND area > 0 "
            "AND (first_seen >= datetime('now', ?) "
            "     OR (scraped_at >= datetime('now', ?) AND first_seen >= datetime('now', ?)))",
            (f"-{hours} hours", f"-{hours} hours", f"-{FRESH_DAYS} days"),
        ).fetchall()
    already = set(load_alerted())
    return [dict(r) for r in rows if r["id"] not in already]


# Лотов на один вызов пакетной оценки: фичи пачки живут в памяти целиком.
ALERT_BATCH_SIZE = 500


def find_good_deals(db_path: Path | str = DB_PATH, hours: int = ALERT_WINDOW_HOURS) -> list[dict]:
    """Новые объявления с вердиктом GOOD_DEAL, отсортированы по выгоде.

    Оценка пакетная: раньше здесь на каждый лот окна шла полная карточка
    (SHAP, подсказки, рынок, аналоги, коммит в базу) — ~0.7 с на лот. После
    фикса SafeLine окно выросло до 4–7 тыс. лотов, шаг — до 80 минут, и
    2026-09-20 он не дал ночному проходу залить базу
    (.kiro/specs/rescrape-post-steps). Вердикт и цена — те же, что в карточке.
    """
    from krisha.db import log_predictions

    started = time.monotonic()
    candidates = new_listings(db_path, hours)
    deals, priced_total = [], 0
    with get_conn(db_path) as conn:
        for start in range(0, len(candidates), ALERT_BATCH_SIZE):
            priced = _price_chunk(candidates[start:start + ALERT_BATCH_SIZE])
            priced_total += len(priced)
            # issue #128: вердикты копим для проверки на судьбе лотов — как и раньше.
            log_predictions([result for _, result in priced], conn=conn)
            deals.extend(
                _deal(listing, result) for listing, result in priced
                if result["verdict"] == "GOOD_DEAL"
            )
    # Подозрительные — в конец: иначе первыми шли бы как раз лоты с −60%,
    # про которые карточка пишет «цена сильно ниже рынка, проверьте».
    deals.sort(key=lambda d: (d["suspicious"], d.get("diff_pct") or 0))
    logger.info(
        "alerts: оценено %s из %s лотов окна за %.1f с, выгодных %s",
        priced_total, len(candidates), time.monotonic() - started, len(deals),
    )
    if candidates and not priced_total:
        # Ни один лот не оценился — это не «сегодня выгодных нет», а поломка
        # (нет модели, сломалась схема фич). С 01.10 по 03.10 так и было: модели
        # переехали в приватный релиз, в джобе их никто не скачивал, и рассылка
        # с каналом три ночи молча выходили пустыми при зелёном ране.
        raise AlertsPricingFailed(f"ни один из {len(candidates)} лотов окна не оценился")
    return deals


class AlertsPricingFailed(RuntimeError):
    """Окно алертов не оценилось целиком — рассылать нечего, нужен админ."""


def _deal(listing: dict, result: dict) -> dict:
    """Выгодный лот для рассылки + та же пометка «подозрительно дёшево», что
    на карточке (scam.assess_scam_risk: глубоко ниже нижней границы и свежий)."""
    from krisha.market import _parse_dt
    from krisha.scam import assess_scam_risk

    first_seen = _parse_dt(listing.get("first_seen"))
    days = (datetime.now(timezone.utc) - first_seen).days if first_seen else None
    risk = assess_scam_risk(result.get("fair_price_low"), listing.get("price"), days)
    return {
        **listing,
        "fair_price": result["fair_price"],
        "diff_pct": result["diff_pct"],
        "suspicious": bool(risk and risk["level"] == "high"),
        "below_low_pct": risk["below_pct"] if risk else None,
    }


def _price_chunk(chunk: list[dict]) -> list[tuple[dict, dict]]:
    """Пачка лотов → [(лот, оценка)]. Кривой лот не должен ронять всю пачку."""
    from krisha.predict import predict_listings_batch

    try:
        return list(zip(chunk, predict_listings_batch(chunk), strict=True))
    except Exception:  # noqa: BLE001 — упала пачка: переоцениваем по одному ниже
        logger.warning("alerts: пачка из %s лотов не оценилась — оцениваю по одному",
                       len(chunk), exc_info=True)
    priced = []
    for listing in chunk:
        try:
            priced.append((listing, predict_listings_batch([listing])[0]))
        except Exception as exc:  # noqa: BLE001 — одна кривая строка не должна ронять рассылку
            logger.warning("alerts: predict для %s не удался: %s", listing.get("id"), exc)
    return priced


def _deals_word(n: int) -> str:
    a, b = n % 100, n % 10
    if 10 < a < 20 or b == 0 or b >= 5:
        return "новых выгодных объявлений"
    return "новое выгодное объявление" if b == 1 else "новых выгодных объявления"


def format_alert_header(deals: list[dict], flt: dict | None = None) -> str:
    """Первое сообщение подборки: сколько нашлось и по какому фильтру."""
    from krisha.subscriptions import describe_filters

    head = f"🔥 <b>{len(deals)} {_deals_word(len(deals))}</b>"
    if flt:
        head += f"\nпо фильтру: {html.escape(describe_filters(flt))}"
    return head


def format_deal_card(d: dict) -> str:
    """Одно выгодное объявление — отдельной карточкой: вердикт, квартира, цена и оценка блоком."""
    district = DISTRICT_RU.get(d.get("district") or "", "")
    title = html.escape(d.get("title") or f"{d.get('rooms', '?')}-комн, {d.get('area', '?')} м²")
    lines = [f"🟢 <b>{pct(d['diff_pct'])} к оценке</b>" + (f" · {district}" if district else ""),
             f"🏠 <a href=\"{html.escape(d['url'])}\">{title}</a>",
             "<pre>" + html.escape(f"Цена    {dec(d['price'] / 1_000_000)} млн ₸\n"
                                   f"Оценка  {dec(d['fair_price'] / 1_000_000)} млн ₸") + "</pre>"]
    if d.get("suspicious"):
        # та же формулировка, что в карточке бота (issue #157)
        lines.append(
            f"🚨 Цена сильно ниже рынка — проверьте внимательнее: на "
            f"{d['below_low_pct']:.0f}% ниже нижней границы справедливого интервала"
        )
    return "\n".join(lines)


def deal_keyboard(d: dict) -> dict | None:
    """Кнопки карточки: открыть объявление и взять его в слежку."""
    row: list[dict] = []
    if str(d.get("url") or "").startswith("http"):
        row.append({"text": "Открыть ↗", "url": d["url"]})
    if d.get("id") is not None and str(d["id"]).isdigit():
        row.append({"text": "👀 Следить", "callback_data": f"track:{d['id']}"})
    return {"inline_keyboard": [row]} if row else None


HEADER_KEYBOARD = {"inline_keyboard": [[
    {"text": "⚙️ Изменить фильтр", "callback_data": "help:alerts"},
    {"text": "🔕 Отписаться", "callback_data": "alerts:off"},
]]}


def format_alert(deals: list[dict], flt: dict | None = None) -> str:
    """Вся подборка одним текстом (печать в сухом прогоне scripts/send_alerts.py):
    заголовок и карточки — то же, что send_alerts шлёт отдельными сообщениями.
    «Отписаться» в рассылке — кнопка под заголовком, поэтому строки с командой здесь нет."""
    parts = [format_alert_header(deals, flt)] + [format_deal_card(d) for d in deals]
    return "\n\n".join(parts)


# Пауза между сообщениями одному чату: Telegram режет больше ~1 сообщения в
# секунду в чат (было 0.4 с — подборка из 6 сообщений ловила 429).
ALERT_CARD_PAUSE_S = 1.1
# Общий темп бота: ~30 сообщений в секунду на всех; держим запас.
ALERT_GLOBAL_PAUSE_S = 0.04
# Потолок ожидания по retry_after: дольше ждать в шаге с таймаутом нельзя.
MAX_RETRY_AFTER_S = 30.0
# Журнал доставки пишем раз в столько успешных карточек (и в finally): шаг
# Actions с таймаутом 15 минут убивался посреди рассылки, а remember_alerted
# стоял только в конце — назавтра те же подборки приходили повторно.
ALERT_SAVE_EVERY = 25

# тесты подменяют оба, чтобы не ждать паузы по-настоящему
_sleep = time.sleep
_now = time.monotonic


class _Pacer:
    """Паузы рассылки: не чаще ALERT_CARD_PAUSE_S в один чат и
    ALERT_GLOBAL_PAUSE_S между любыми сообщениями."""

    def __init__(self) -> None:
        self._last_chat: dict[str, float] = {}
        self._last_any: float | None = None

    def wait(self, chat_id: Any) -> None:
        now = _now()
        due = now
        last = self._last_chat.get(str(chat_id))
        if last is not None:
            due = max(due, last + ALERT_CARD_PAUSE_S)
        if self._last_any is not None:
            due = max(due, self._last_any + ALERT_GLOBAL_PAUSE_S)
        if due > now:
            _sleep(due - now)
        stamp = _now()
        self._last_chat[str(chat_id)] = stamp
        self._last_any = stamp


def chat_is_gone(resp: dict | None) -> bool:
    """Чат недоступен навсегда: бот заблокирован/выгнан, пользователь удалён,
    чата нет. Повторять отправку туда бессмысленно — подписку надо убирать."""
    if not isinstance(resp, dict) or resp.get("ok"):
        return False
    code = resp.get("error_code")
    desc = str(resp.get("description") or "").lower()
    if code == 403:
        return True
    return code == 400 and ("chat not found" in desc or "user not found" in desc)


def deliver(chat_id: Any, pacer: _Pacer | None = None, **payload: Any) -> tuple[dict | None, int, str]:
    """sendMessage с одним повтором на 429 (по parameters.retry_after) и
    переездом группы (migrate_to_chat_id → новый id, подписка и слежка за ним).

    Возвращает (ответ, фактический id чата, статус); статус "ok", "gone" —
    чат мёртв, подписка и слежка убраны, "error" — прочий сбой.
    """
    from krisha.bot import tg_call
    from krisha.subscriptions import forget_chat, migrate_chat

    chat = int(chat_id)
    resp: dict | None = None
    retried = migrated = False
    while True:
        if pacer is not None:
            pacer.wait(chat)
        resp = tg_call("sendMessage", chat_id=chat, **payload)
        if resp and resp.get("ok"):
            return resp, chat, "ok"
        params = (resp or {}).get("parameters") or {}
        new_chat = params.get("migrate_to_chat_id")
        if new_chat and not migrated:
            migrated = True
            try:
                migrate_chat(chat, int(new_chat))
            except Exception:  # noqa: BLE001 — перенос состояния не должен ронять рассылку
                logger.warning("alerts: не удалось перенести состояние чата", exc_info=True)
            chat = int(new_chat)
            continue
        if (resp or {}).get("error_code") == 429 and not retried:
            retried = True
            try:
                wait = float(params.get("retry_after") or 1)
            except (TypeError, ValueError):
                wait = 1.0
            _sleep(min(max(wait, 0.0), MAX_RETRY_AFTER_S) + 0.1)
            continue
        if chat_is_gone(resp):
            try:
                forget_chat(chat)
            except Exception:  # noqa: BLE001
                logger.warning("alerts: не удалось убрать мёртвый чат", exc_info=True)
            return resp, chat, "gone"
        return resp, chat, "error"


class _DeliveryLog:
    """Журнал доставки рассылки: что ушло какому чату, и какие лоты дошли до
    всех, кому были выбраны. Сохраняется кусками (checkpoint) и в finally."""

    def __init__(self, persist: bool) -> None:
        self.persist = persist
        self.by_chat = load_alerted_chats()
        self.selected: dict[int, set[str]] = {}
        self.delivered: dict[int, set[str]] = {}
        self.dead: set[str] = set()
        self.done: set[int] = set()
        self._dirty: set[str] = set()
        self._deleted: set[str] = set()
        self._unsaved = 0

    def seen(self, chat: str) -> set[int]:
        return set(self.by_chat.get(chat, ()))

    def select(self, chat: str, lot: int) -> None:
        self.selected.setdefault(lot, set()).add(chat)

    def record(self, chat: str, actual_chat: int, lot: int) -> None:
        self.delivered.setdefault(lot, set()).add(chat)
        key = str(actual_chat)
        if key != chat and chat in self.by_chat:
            # группа переехала: журнал — за новым id
            self.by_chat[key] = self.by_chat.pop(chat) + self.by_chat.get(key, [])
            self._deleted.add(chat)
        ids = self.by_chat.setdefault(key, [])
        if lot not in ids:
            ids.append(lot)
            del ids[:-ALERTED_CHAT_KEEP]
        self._dirty.add(key)
        self._unsaved += 1
        if self._unsaved >= ALERT_SAVE_EVERY:
            self.flush()

    def drop(self, chat: str) -> None:
        self.dead.add(chat)
        if self.by_chat.pop(chat, None) is not None:
            self._deleted.add(chat)
            self._dirty.discard(chat)

    def _complete(self) -> list[int]:
        out = []
        for lot, chats in self.selected.items():
            if lot in self.done:
                continue
            need = chats - self.dead
            if need and need <= self.delivered.get(lot, set()):
                out.append(lot)
        return out

    def flush(self) -> None:
        self._unsaved = 0
        if not self.persist:
            return
        from krisha.subscriptions import save_json_state

        if self._dirty or self._deleted:
            save_json_state(
                _alerted_chats_path(), self.by_chat, "state: журнал рассылки алертов",
                deleted_keys=set(self._deleted), touched_keys=set(self._dirty),
            )
            self._dirty.clear()
            self._deleted.clear()
        complete = self._complete()
        if complete:
            # Помечаем только реально доставленные: окно ловит лот и по
            # scraped_at, так что без этой отметки очередь дообновления деталей
            # (issue #102) присылала бы один и тот же лот снова и снова.
            remember_alerted(sorted(complete))
            self.done.update(complete)


def send_alerts(
    subscriptions: dict[str, dict], deals: list[dict], persist: bool = True
) -> int:
    """Рассылает подборки: заголовок и по карточке с кнопками на каждое объявление.
    Возвращает число чатов, которым подборка дошла.

    Сообщения идут по кругу (заголовки всем, потом первая карточка всем…):
    пауза ≥ ALERT_CARD_PAUSE_S нужна между сообщениями ОДНОМУ чату, и подряд
    по чату 200 подписчиков × 6 сообщений не влезали в таймаут шага.
    """
    log = _DeliveryLog(persist)
    plans: list[dict[str, Any]] = []
    for chat_id, flt in subscriptions.items():
        chat = str(chat_id)
        seen = log.seen(chat)
        matched = [
            d for d in deals
            if match_filters(d, flt) and not (d.get("id") is not None and int(d["id"]) in seen)
        ][:MAX_DEALS_PER_CHAT]
        if not matched:
            continue
        for d in matched:
            if d.get("id") is not None:
                log.select(chat, int(d["id"]))
        plans.append({"chat": chat, "target": int(chat_id), "flt": flt, "deals": matched})

    pacer = _Pacer()
    sent = 0
    try:
        active = []
        for plan in plans:
            _, target, status = deliver(plan["target"], pacer,
                                        text=format_alert_header(plan["deals"], plan["flt"]),
                                        parse_mode="HTML", reply_markup=HEADER_KEYBOARD)
            plan["target"] = target
            if status == "ok":
                sent += 1
                active.append(plan)
            elif status == "gone":
                log.drop(plan["chat"])
        for i in range(MAX_DEALS_PER_CHAT):
            for plan in active:
                if plan.get("stop") or i >= len(plan["deals"]):
                    continue
                d = plan["deals"][i]
                extra = {}
                keyboard = deal_keyboard(d)
                if keyboard:
                    extra["reply_markup"] = keyboard
                _, target, status = deliver(plan["target"], pacer, text=format_deal_card(d),
                                            parse_mode="HTML", disable_web_page_preview=True,
                                            **extra)
                plan["target"] = target
                if status == "ok" and d.get("id") is not None:
                    log.record(plan["chat"], target, int(d["id"]))
                elif status == "gone":
                    plan["stop"] = True
                    log.drop(plan["chat"])
    finally:
        log.flush()
    return sent
