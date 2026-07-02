"""Публичный Telegram-канал: «Топ-5 выгодных лотов дня» (задача 7 бэклога).

Постит после ночного рескрейпа дайджест лучших GOOD_DEAL-лотов в канал из
env `TG_CHANNEL_ID` (@имя_канала или -100…; бот должен быть админом канала).
Чтобы не постить один лот дважды, id опубликованных хранятся в
data/channel_posted.json тем же механизмом, что подписки (коммит в GitHub).
"""

from __future__ import annotations

import html
import json
import logging
import os
from typing import Any

from krisha.config import ROOT_DIR
from krisha.stats import DISTRICT_RU
from krisha.subscriptions import save_json_state

logger = logging.getLogger(__name__)

CHANNEL_ENV = "TG_CHANNEL_ID"
POSTED_PATH = ROOT_DIR / "data" / "channel_posted.json"
DIGEST_SIZE = 5
POSTED_KEEP = 500  # сколько последних id помним


def load_posted(path=None) -> list[int]:
    path = path or POSTED_PATH
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text()))
    except (json.JSONDecodeError, TypeError):
        logger.warning("channel: битый %s — начинаем с нуля", path)
        return []


def format_digest(deals: list[dict[str, Any]]) -> str:
    """HTML-пост для канала."""
    lines = [f"🔥 <b>Топ-{len(deals)} выгодных лотов дня</b>", ""]
    for i, d in enumerate(deals, 1):
        title = html.escape(d.get("title") or f"{d.get('rooms', '?')}-комн, {d.get('area', '?')} м²")
        district = DISTRICT_RU.get(d.get("district") or "", "")
        lines.append(f"{i}. <a href=\"{html.escape(d['url'])}\">{title}</a>")
        detail = (f"   💰 {d['price'] / 1e6:.1f} млн ₸ · оценка модели "
                  f"{d['fair_price'] / 1e6:.1f} млн ({d['diff_pct']:+.1f}%)")
        if district:
            detail += f" · {district}"
        lines.append(detail)
        lines.append("")
    lines.append("🤖 Оценка — модель krisha-fair-price. Проверяйте лот перед сделкой.")
    return "\n".join(lines)


def post_channel_digest(
    deals: list[dict[str, Any]], dry_run: bool = False, persist: bool = True
) -> str | None:
    """Выбирает непубликовавшиеся лоты и постит дайджест. Возвращает текст поста."""
    channel = os.environ.get(CHANNEL_ENV)
    if not channel and not dry_run:
        logger.info("%s не задан — пост в канал пропущен", CHANNEL_ENV)
        return None

    posted = load_posted()
    fresh = [d for d in deals if d.get("id") not in set(posted)][:DIGEST_SIZE]
    if not fresh:
        logger.info("channel: новых лотов для дайджеста нет")
        return None

    text = format_digest(fresh)
    if dry_run:
        return text

    from krisha.bot import tg_call

    resp = tg_call("sendMessage", chat_id=channel, text=text,
                   parse_mode="HTML", disable_web_page_preview=True)
    if not (resp and resp.get("ok")):
        logger.warning("channel: пост не отправился: %s", resp)
        return None
    if persist:
        new_posted = (posted + [d["id"] for d in fresh])[-POSTED_KEEP:]
        save_json_state(POSTED_PATH, new_posted,
                        "state: опубликованные в канал лоты")
    return text
