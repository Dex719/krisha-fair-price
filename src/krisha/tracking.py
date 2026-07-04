"""/track — слежка за конкретными объявлениями.

Пользователь командой /track <ссылка> подписывается на лот; после каждого
рескрейпа (scripts/send_alerts.py) сравниваем текущее состояние лота в базе
с последним, о котором уведомляли, и шлём алерт при изменении цены или
снятии с продажи.

Хранение: data/tracked.json (тот же механизм, что subscriptions.json —
локальный файл + коммит в GitHub через Contents API, см. subscriptions.py).

Формат: {"<chat_id>": {"<listing_id>": {"price": int|null, "title": str|null,
"since": iso}}}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krisha.config import DATA_DIR, DB_PATH
from krisha.db import get_conn

logger = logging.getLogger(__name__)

TRACKED_PATH = DATA_DIR / "tracked.json"
MAX_TRACKED_PER_CHAT = 10


def load_tracked(path: Path | None = None) -> dict[str, dict[str, Any]]:
    from krisha.subscriptions import load_json_state

    data = load_json_state(path or TRACKED_PATH)
    return data if isinstance(data, dict) else {}


def _save(tracked: dict[str, Any], message: str, path: Path | None = None) -> None:
    from krisha.subscriptions import save_json_state

    save_json_state(path or TRACKED_PATH, tracked, message)


def add_tracked(
    chat_id: int,
    listing_id: int,
    price: int | None,
    title: str | None,
    path: Path | None = None,
) -> tuple[bool, str | None]:
    """Добавляет лот в слежку. Возвращает (успех, причина отказа)."""
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
    }
    # Без chat_id/listing_id в message: история коммитов публична
    _save(tracked, "track: обновление слежки", path)
    return True, None


def remove_tracked(chat_id: int, listing_id: int | None, path: Path | None = None) -> int:
    """Убирает лот (или все лоты чата при listing_id=None). Возвращает число удалённых."""
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
    _save(tracked, "track: обновление слежки", path)
    return removed


def list_tracked(chat_id: int, path: Path | None = None) -> dict[str, Any]:
    return load_tracked(path).get(str(chat_id), {})


def check_tracked_updates(
    db_path=DB_PATH, path: Path | None = None, persist: bool = True
) -> list[tuple[int, str]]:
    """Сравнивает лоты в слежке с базой после рескрейпа.

    Возвращает [(chat_id, html-сообщение), ...] и обновляет сохранённые цены
    (persist=False — не сохранять, для dry-run), чтобы не слать одно и то же
    изменение повторно.
    """
    tracked = load_tracked(path)
    if not tracked:
        return []

    messages: list[tuple[int, str]] = []
    changed = False
    with get_conn(db_path) as conn:
        for chat_id, lots in tracked.items():
            events: list[str] = []
            for lid, state in list(lots.items()):
                row = conn.execute(
                    "SELECT price, is_active, first_seen, last_seen, title, url "
                    "FROM listings WHERE id = ?",
                    (int(lid),),
                ).fetchone()
                if row is None:
                    continue
                event = _lot_event(lid, state, row)
                if event is None:
                    continue
                events.append(event)
                changed = True
                if not row["is_active"]:
                    del lots[lid]  # снят с продажи — слежка закончена
                else:
                    state["price"] = row["price"]
                    state["title"] = state.get("title") or row["title"]
            if events:
                messages.append((int(chat_id), "\n\n".join(events)))

    if changed and persist:
        _save(tracked, "track: обновление цен после рескрейпа", path)
    return messages


def _lot_event(lid: str, state: dict[str, Any], row) -> str | None:
    """Одно событие по лоту: изменение цены или снятие. Нет событий → None."""
    import html as _html

    title = _html.escape(state.get("title") or row["title"] or f"Объявление {lid}")
    url = row["url"] or f"https://krisha.kz/a/show/{lid}"
    link = f'<a href="{_html.escape(url)}">{title}</a>'

    if not row["is_active"]:
        days = _days_between(row["first_seen"], row["last_seen"])
        days_txt = f" (провисело ~{days} дн.)" if days is not None else ""
        return f"🏁 {link}\nСнято с продажи{days_txt} — слежку завершил."

    old, new = state.get("price"), row["price"]
    if new is None or old is None or int(new) == int(old):
        return None
    diff_pct = (new - old) / old * 100 if old else 0
    arrow = "📉" if new < old else "📈"
    return (
        f"{arrow} {link}\nЦена: {_fmt_mln(old)} → <b>{_fmt_mln(new)}</b> "
        f"({diff_pct:+.1f}%)"
    )


def _fmt_mln(value: int | float) -> str:
    return f"{value / 1_000_000:.1f} млн ₸"


def _days_between(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
    except ValueError:
        return None
    return max((e - s).days, 0)
