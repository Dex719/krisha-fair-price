"""/track — слежка за конкретными объявлениями.

Пользователь командой /track <ссылка> подписывается на лот; после каждого
рескрейпа (scripts/send_alerts.py) сравниваем текущее состояние лота в базе
с последним, о котором уведомляли, и шлём алерт при изменении цены или
снятии с продажи.

Продажа и аренда проверяются раздельно: у каждой своя база и свой обход
(утренний rescrape.yml и вечерний rescrape-rent.yml), поэтому
check_tracked_updates смотрит только лоты своего типа (deal).

Хранение: data/tracked.json (тот же механизм, что subscriptions.json —
локальный файл + коммит в GitHub через Contents API, см. subscriptions.py).

Формат: {"<chat_id>": {"<listing_id>": {"price": int|null, "title": str|null,
"since": iso, "deal"?: "arenda"}}} — без "deal" лот считается продажей
(так хранились все лоты до слежки за арендой).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krisha.config import DATA_DIR, DB_PATH, MAX_TRUSTED_DELIST_LAG_DAYS
from krisha.db import get_conn
from krisha.numfmt import dec, pct

logger = logging.getLogger(__name__)

TRACKED_PATH = DATA_DIR / "tracked.json"
MAX_TRACKED_PER_CHAT = 10
SALE, RENT = "prodazha", "arenda"


def lot_deal(state: dict[str, Any]) -> str:
    """Тип сделки лота в слежке; старые записи без поля — продажа."""
    return RENT if state.get("deal") == RENT else SALE

# issue #156: порог доверия к снятию — общий с market.py, см. config.
# Здесь цена ложного срабатывания особенно несимметрична: отправленное
# «🏁 Снято с продажи» не отзовёшь, и вместе с ним лот молча выпадает из
# слежки — пользователь узнает об этом, только когда перестанет получать
# алерты по живому объявлению. Так было бы в окне слепоты 14–26.07.2026
# (13 дней) и раньше 02.07 (лаг ≈ 21 день у 1901 лота).


def load_tracked(path: Path | None = None) -> dict[str, dict[str, Any]]:
    from krisha.subscriptions import load_json_state

    data = load_json_state(path or TRACKED_PATH)
    return data if isinstance(data, dict) else {}


def _save(
    tracked: dict[str, Any],
    message: str,
    path: Path | None = None,
    deleted_keys: set[str] | None = None,
    touched_keys: set[str] | None = None,
    update_only: bool = False,
) -> dict[str, Any]:
    """Сохраняет tracked.json; возвращает итог после слияния с сервером.

    Ключи слияния — пары "чат:лот" (см. subscriptions._merge_remote): два
    воркера uvicorn с внутрипроцессным STATE_LOCK иначе теряли лот, взятый
    в слежку в соседнем процессе секундой раньше.
    """
    from krisha.subscriptions import save_json_state

    written = save_json_state(
        path or TRACKED_PATH, tracked, message, deleted_keys=deleted_keys,
        touched_keys=touched_keys, update_only=update_only, normalize=_enforce_limit,
    )
    return written if isinstance(written, dict) else tracked


def _lot_key(chat_id: Any, listing_id: Any) -> str:
    return f"{chat_id}:{listing_id}"


def _enforce_limit(tracked: Any) -> Any:
    """Лимит MAX_TRACKED_PER_CHAT после слияния: два «Следить» с разных
    воркеров по отдельности проходят проверку, а вместе дают 11 лотов.
    Оставляем самые ранние по since — опоздавший лот не пройдёт, и add_tracked
    скажет пользователю «лимит», а не «слежу»."""
    if not isinstance(tracked, dict):
        return tracked
    out = {}
    for chat, lots in tracked.items():
        if isinstance(lots, dict) and len(lots) > MAX_TRACKED_PER_CHAT:
            keep = sorted(lots, key=lambda lid: (str(lots[lid].get("since") or ""), lid)
                          if isinstance(lots[lid], dict) else ("", lid))
            lots = {lid: lots[lid] for lid in keep[:MAX_TRACKED_PER_CHAT]}
        out[chat] = lots
    return out


def _refresh(path: Path | None) -> None:
    """Перед правкой из бота — свежая копия с сервера (fail-soft).

    Space читает состояние один раз при старте, а ночью Actions обновляют
    базовые цены и убирают снятые лоты. Правка поверх старой копии своего же
    чата вернула бы старую цену — и тот же алерт пришёл бы второй раз.
    """
    if path is not None:  # явный путь — тесты и разовые скрипты, не прод-файл
        return
    from krisha.subscriptions import pull_state

    pull_state((TRACKED_PATH.name,))


def add_tracked(
    chat_id: int,
    listing_id: int,
    price: int | None,
    title: str | None,
    path: Path | None = None,
    deal: str = SALE,
) -> tuple[bool, str | None]:
    """Добавляет лот в слежку. Возвращает (успех, причина отказа)."""
    from krisha.subscriptions import STATE_LOCK

    # Под локом весь цикл читать-менять-писать: два /track из разных чатов
    # обрабатываются параллельно (BackgroundTasks поверх тредпула), и без
    # него оба читают одну версию файла, а записавший вторым теряет чужой лот.
    with STATE_LOCK:
        _refresh(path)
        tracked = load_tracked(path)
        chat = tracked.setdefault(str(chat_id), {})
        if str(listing_id) in chat:
            return False, "already"
        if len(chat) >= MAX_TRACKED_PER_CHAT:
            return False, "limit"
        chat[str(listing_id)] = {
            "price": price,
            "title": title,
            "since": datetime.now(timezone.utc).isoformat(),
            **({"deal": RENT} if deal == RENT else {}),
        }
        # Без chat_id/listing_id в message: история коммитов публична
        written = _save(tracked, "track: обновление слежки", path,
                        touched_keys={_lot_key(chat_id, listing_id)})
        if str(listing_id) not in (written.get(str(chat_id)) or {}):
            # Соседний воркер за это время добил чат до лимита — наш лот
            # отсечён при слиянии (_enforce_limit).
            return False, "limit"
        return True, None


def remove_tracked(chat_id: int, listing_id: int | None, path: Path | None = None) -> int:
    """Убирает лот (или все лоты чата при listing_id=None). Возвращает число удалённых."""
    from krisha.subscriptions import STATE_LOCK

    with STATE_LOCK:
        _refresh(path)
        tracked = load_tracked(path)
        chat = tracked.get(str(chat_id))
        if not chat:
            return 0
        if listing_id is None:
            removed = len(chat)
            del tracked[str(chat_id)]
            # весь чат — удаление ключа верхнего уровня (issue #111)
            gone = {str(chat_id)}
        else:
            if str(listing_id) not in chat:
                return 0
            del chat[str(listing_id)]
            removed = 1
            if not chat:
                del tracked[str(chat_id)]
            # Удаляем ровно пару чат+лот: лот, который соседний воркер только
            # что добавил в тот же чат, должен уцелеть.
            gone = {_lot_key(chat_id, listing_id)}
        _save(tracked, "track: обновление слежки", path, deleted_keys=gone, touched_keys=set())
        return removed


def move_tracked(old_chat_id: Any, new_chat_id: Any, path: Path | None = None) -> int:
    """Группа стала супергруппой: слежка переезжает на новый id чата."""
    from krisha.subscriptions import STATE_LOCK

    old, new = str(old_chat_id), str(new_chat_id)
    with STATE_LOCK:
        _refresh(path)
        tracked = load_tracked(path)
        lots = tracked.pop(old, None)
        if not lots:
            return 0
        target = tracked.setdefault(new, {})
        for lid, state in lots.items():
            target.setdefault(lid, state)
        _save(tracked, "track: обновление слежки", path, deleted_keys={old},
              touched_keys={_lot_key(new, lid) for lid in target})
        return len(lots)


def list_tracked(chat_id: int, path: Path | None = None) -> dict[str, Any]:
    return load_tracked(path).get(str(chat_id), {})


def check_tracked_updates(
    db_path=DB_PATH,
    path: Path | None = None,
    persist: bool = True,
    only_chats: set[int] | None = None,
    deal: str = SALE,
) -> list[tuple[int, str]]:
    """Сравнивает лоты в слежке с базой после рескрейпа.

    `deal` — какие лоты проверять: продажу по базе продажи (db_path=DB_PATH)
    или аренду по базе аренды (db_path=RENT_DB_PATH). Лоты другого типа не
    трогаем: их база в этом проходе не обновлялась.

    Возвращает [(chat_id, html-сообщение), ...] и обновляет сохранённые цены
    (persist=False — не сохранять, для dry-run), чтобы не слать одно и то же
    изменение повторно.

    `only_chats` — применить и сохранить изменения ТОЛЬКО для этих чатов.
    Нужно для порядка «сначала доставили, потом зафиксировали»: если
    сохранить состояние до отправки, а отправка упадёт (Telegram 5xx, бот
    заблокирован), пользователь не узнает об изменении цены НИКОГДА — на
    следующем проходе старая и новая цена уже совпадают. См. send_alerts.py.
    """
    tracked = load_tracked(path)
    if not tracked:
        return []

    messages: list[tuple[int, str]] = []
    # Сохраняем только пары чат+лот, которые этот проход реально поменял, и
    # только поверх того, что лежит на сервере в момент записи (update_only).
    # tracked.json скачан в начале шага Actions; /track и /untrack, сделанные
    # за минуты рассылки, раньше откатывались записью этой устаревшей копии.
    touched: set[str] = set()
    removed: set[str] = set()
    with get_conn(db_path) as conn:
        for chat_id, lots in tracked.items():
            if only_chats is not None and int(chat_id) not in only_chats:
                continue
            events: list[str] = []
            for lid, state in list(lots.items()):
                if deal == SALE and lot_deal(state) == RENT:
                    continue
                legacy = deal == RENT and lot_deal(state) == SALE
                if legacy and state.get("deal") is not None:
                    continue
                row = conn.execute(
                    "SELECT price, is_active, first_seen, last_seen, delisted_at, "
                    "title, url FROM listings WHERE id = ?",
                    (int(lid),),
                ).fetchone()
                if row is None:
                    continue
                if legacy:
                    # До слежки за арендой /track брал аренду как продажу: такие лоты
                    # висели без алертов. id на krisha общий, так что раз лот нашёлся
                    # в базе аренды — это аренда; помечаем и дальше проверяем как её.
                    state["deal"] = RENT
                    touched.add(_lot_key(chat_id, lid))
                event = _lot_event(lid, state, row, rent=deal == RENT)
                if not row["is_active"]:
                    if event is not None:
                        events.append(event)
                        del lots[lid]  # снят с продажи — слежка закончена
                        removed.add(_lot_key(chat_id, lid))
                        touched.discard(_lot_key(chat_id, lid))
                    continue
                # Цену активного лота подтягиваем из базы ВСЕГДА, а не только
                # когда есть что отправить. Лот, взятый в слежку с неизвестной
                # ценой (price=None — деталь ещё не докачана, страница не
                # открылась), иначе навсегда застревал: _lot_event выходит по
                # `old is None`, событий нет, а раз событий нет — цена не
                # сохранялась, и на следующем проходе old снова None. Такой
                # лот не давал алерта об изменении цены никогда.
                if state.get("price") != row["price"] or (
                    not state.get("title") and row["title"]
                ):
                    state["price"] = row["price"]
                    state["title"] = state.get("title") or row["title"]
                    touched.add(_lot_key(chat_id, lid))
                if event is not None:
                    events.append(event)
            if events:
                messages.append((int(chat_id), "\n\n".join(events)))

    if (touched or removed) and persist:
        _save(tracked, "track: обновление цен после рескрейпа", path,
              deleted_keys=removed, touched_keys=touched, update_only=True)
    return messages


def _lot_event(lid: str, state: dict[str, Any], row, rent: bool = False) -> str | None:
    """Одно событие по лоту: изменение цены или снятие. Нет событий → None."""
    import html as _html

    title = _html.escape(state.get("title") or row["title"] or f"Объявление {lid}")
    url = row["url"] or f"https://krisha.kz/a/show/{lid}"
    link = f'<a href="{_html.escape(url)}">{title}</a>'

    if not row["is_active"]:
        # issue #156: снятие, замеченное после долгого перерыва в наблюдении,
        # не является фактом о рынке — мы просто не смотрели. Молчим и
        # ОСТАВЛЯЕМ лот в слежке (вызывающий удаляет только когда event не
        # None): если лот жив, ближайший нормальный проход вернёт is_active=1
        # и слежка продолжится как ни в чём не бывало.
        lag = _days_between(row["last_seen"], row["delisted_at"])
        if lag is not None and lag > MAX_TRUSTED_DELIST_LAG_DAYS:
            logger.info(
                "Лот %s: снят после %s дн. без наблюдения (порог %s) — "
                "алерт о снятии не шлём, слежку сохраняем",
                lid, lag, MAX_TRUSTED_DELIST_LAG_DAYS,
            )
            return None
        days = _days_between(row["first_seen"], row["last_seen"])
        days_txt = f" (провисело ~{days} дн.)" if days is not None else ""
        gone = "Объявление об аренде снято" if rent else "Снято с продажи"
        return f"🏁 {link}\n{gone}{days_txt} — слежку завершил."

    old, new = state.get("price"), row["price"]
    if new is None or old is None or int(new) == int(old):
        return None
    diff_pct = (new - old) / old * 100 if old else 0
    down = new < old
    arrow = "📉" if down else "📈"
    fmt = _fmt_rent if rent else _fmt_mln
    what = ("Аренду" if rent else "Цену") + (" снизили" if down else " подняли")
    step = abs(new - old)
    step_txt = _fmt_rent(step) if rent else (f"{step / 1_000:.0f} тыс ₸" if step < 950_000 else _fmt_mln(step))
    # Было / Стало — выровненным блоком, как цифры в оценке бота
    block = _html.escape(f"Было   {fmt(old)}\nСтало  {fmt(new)}")
    return (
        f"{arrow} <b>{what} на {step_txt}</b> ({pct(diff_pct)})\n"
        f"🏠 {link}\n<pre>{block}</pre>"
    )


def _fmt_mln(value: int | float) -> str:
    return f"{dec(value / 1_000_000)} млн ₸"


def _fmt_rent(value: int | float) -> str:
    return f"{value / 1_000:.0f} тыс ₸/мес"


def _days_between(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
    except ValueError:
        return None
    return max((e - s).days, 0)
