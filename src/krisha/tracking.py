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
) -> None:
    from krisha.subscriptions import save_json_state

    save_json_state(
        path or TRACKED_PATH, tracked, message, deleted_keys=deleted_keys, touched_keys=touched_keys,
    )


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
        _save(tracked, "track: обновление слежки", path, touched_keys={str(chat_id)})
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
        else:
            if str(listing_id) not in chat:
                return 0
            del chat[str(listing_id)]
            removed = 1
            if not chat:
                del tracked[str(chat_id)]
        # Если чат ушёл целиком — это удаление ключа верхнего уровня, о котором
        # надо сказать слиянию (issue #111), иначе он вернётся с сервера.
        gone = {str(chat_id)} if str(chat_id) not in tracked else None
        _save(tracked, "track: обновление слежки", path, deleted_keys=gone,
              touched_keys={str(chat_id)})
        return removed


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
    # Сохраняем только чаты, которые этот проход реально поменял: остальные
    # мог за это время поправить бот на Space (/track), и наша копия их старее.
    touched: set[str] = set()
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
                    touched.add(chat_id)
                event = _lot_event(lid, state, row, rent=deal == RENT)
                if not row["is_active"]:
                    if event is not None:
                        events.append(event)
                        del lots[lid]  # снят с продажи — слежка закончена
                        touched.add(chat_id)
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
                    touched.add(chat_id)
                if event is not None:
                    events.append(event)
            if events:
                messages.append((int(chat_id), "\n\n".join(events)))

    if touched and persist:
        _save(tracked, "track: обновление цен после рескрейпа", path, touched_keys=touched)
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
    arrow = "📉" if new < old else "📈"
    fmt, what = (_fmt_rent, "Аренда") if rent else (_fmt_mln, "Цена")
    return (
        f"{arrow} {link}\n{what}: {fmt(old)} → <b>{fmt(new)}</b> "
        f"({diff_pct:+.1f}%)"
    )


def _fmt_mln(value: int | float) -> str:
    return f"{value / 1_000_000:.1f} млн ₸"


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
