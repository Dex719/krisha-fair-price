"""Ежедневный админ-отчёт после рескрейпа → TG_ADMIN_CHAT_ID.

Утро (продажа, из send_alerts.py после рескрейпа) и вечер (аренда, отдельный
шаг в rescrape-rent.yml: `python -m krisha.daily_report --scope rent`).

Содержимое: счётчики прохода (--summary-json рескрейпа), размер базы,
версия/метрики модели и статистика пользователей за день/неделю/месяц.
Fail-soft: отчёт не должен ронять пайплайн рескрейпа.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from krisha.config import DB_PATH, MODEL_META_PATH, RENT_DB_PATH
from krisha.db import get_conn
from krisha.monitoring import ADMIN_CHAT_ENV
from krisha.usage import ALMATY_TZ, load_state

logger = logging.getLogger(__name__)

# Пути по умолчанию — как в rescrape.yml / rescrape-rent.yml (--summary-json)
SALE_SUMMARY_PATH = Path("/tmp/rescrape_stats.json")
RENT_SUMMARY_PATH = Path("/tmp/rescrape_rent_stats.json")

_SCOPES = {
    "sale": ("🌅 Утренний отчёт: продажа", DB_PATH, SALE_SUMMARY_PATH),
    "rent": ("🌆 Вечерний отчёт: аренда", RENT_DB_PATH, RENT_SUMMARY_PATH),
}


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _db_counts(db_path: Path) -> tuple[int, int] | None:
    """(активных, всего) объявлений в базе или None, если базы нет."""
    if not Path(db_path).exists():
        return None
    try:
        # issue #115: get_conn (WAL/busy_timeout) вместо голого sqlite3.connect —
        # отчёт читает базу сразу после рескрейпа, пока тот ещё может дописывать.
        with get_conn(db_path) as conn:
            active = conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        return active, total
    except sqlite3.OperationalError:
        return None


def _model_line() -> str | None:
    meta = _load_json(MODEL_META_PATH)
    if not meta:
        return None
    from krisha import __version__

    m = (meta.get("metrics") or {}).get("model") or {}
    parts = [f"v{__version__}"]
    if m.get("mape"):
        parts.append(f"MAPE {m['mape'] * 100:.1f}%")
    if m.get("r2"):
        parts.append(f"R² {m['r2']:.2f}")
    return "Модель: " + ", ".join(parts)


def _delisted_fragment(stats: dict) -> str:
    if "delisted" not in stats:
        return "снято ?"
    delisted = stats.get("delisted")
    if delisted is None:
        return "детект снятий: n/a"
    return f"снято {delisted}"


def _usage_lines(now: datetime | None = None) -> list[str]:
    """Событий/уникальных бота за сегодня, 7 и 30 дней из usage_stats."""
    state = load_state()
    days: dict = state.get("days") or {}
    if not days:
        return []
    now = now or datetime.now(ALMATY_TZ)

    def window(n: int) -> tuple[int, int]:
        keys = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]
        rows = [days[k] for k in keys if k in days]
        events = sum(r.get("site", 0) + r.get("predict", 0) + r.get("bot", 0) for r in rows)
        uniq = len({u for r in rows for u in r.get("bot_users", [])})
        return events, uniq

    out = []
    for label, n in (("сегодня", 1), ("за 7 дней", 7), ("за 30 дней", 30)):
        events, uniq = window(n)
        out.append(f"  {label}: <b>{events}</b> событий, {uniq} юзеров бота")
    return ["Пользователи:", *out]


def build_daily_report(scope: str = "sale", summary_path: Path | str | None = None) -> str:
    title, db_path, default_summary = _SCOPES[scope]
    now = datetime.now(ALMATY_TZ)
    lines = [f"<b>{title}</b> · {now.strftime('%d.%m %H:%M')}"]

    stats = _load_json(Path(summary_path) if summary_path else default_summary)
    if stats:
        lines.append(
            f"Проход: в выдаче <b>{stats.get('found_in_search', '?')}</b>, "
            # discovered_new — реально впервые увиденные лоты; new_listings
            # (докачанные детали из общей очереди) оставлен как фолбэк для
            # summary-JSON, снятых до появления нового ключа.
            f"новых <b>{stats.get('discovered_new', stats.get('new_listings', '?'))}</b>, "
            f"изменений цены {stats.get('price_changes', '?')}, "
            f"{_delisted_fragment(stats)}"
        )
        if stats.get("failed_shards"):
            lines.append("⚠️ Сбойные шарды: " + ", ".join(stats["failed_shards"]))
        # issue #127: очередь detail fetch — сколько лотов уже есть (sighting),
        # но детали ещё не докачаны. Растущая очередь = приток обгоняет лимит.
        queue_after = stats.get("detail_queue_after")
        if queue_after:
            lines.append(f"📥 Очередь деталей: {queue_after}")
        if stats.get("suspicious"):
            active_before = stats.get("active_in_db_before")
            median = stats.get("parse_rate_median_7")
            baseline = (
                f"{active_before} активных в БД до прохода"
                if active_before is not None
                else f"медианы {median if median is not None else '?'} последних 7 проходов"
            )
            lines.append(
                "🚨 Parse-rate просел: в выдаче "
                f"{stats.get('found_in_search', '?')} против {baseline} — "
                "проверьте вёрстку/блокировку, db-latest не обновлён"
            )
    else:
        lines.append("Проход: счётчики не найдены (summary-json отсутствует)")

    counts = _db_counts(db_path)
    if counts:
        lines.append(f"База: активных <b>{counts[0]}</b> (всего {counts[1]})")

    if scope == "sale":
        model = _model_line()
        if model:
            lines.append(model)
        lines.extend(_usage_lines(now))

    return "\n".join(lines)


def send_daily_report(
    scope: str = "sale", summary_path: Path | str | None = None, dry_run: bool = False
) -> bool:
    """Шлёт отчёт в TG_ADMIN_CHAT_ID. False — чат не задан или отправка не удалась."""
    try:
        text = build_daily_report(scope, summary_path)
    except Exception:  # noqa: BLE001 — отчёт не должен ронять рескрейп
        logger.exception("daily_report: не удалось собрать отчёт")
        return False
    if dry_run:
        print(text)
        return True
    chat_id = os.environ.get(ADMIN_CHAT_ENV)
    if not chat_id:
        logger.info("%s не задан — ежедневный отчёт не отправляем", ADMIN_CHAT_ENV)
        return False
    from krisha.bot import tg_call

    resp = tg_call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")
    ok = bool(resp and resp.get("ok"))
    if not ok:
        logger.warning("daily_report: отправка не удалась: %s", resp)
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Ежедневный админ-отчёт в Telegram")
    parser.add_argument("--scope", choices=list(_SCOPES), default="sale")
    parser.add_argument("--summary", help="Путь к summary-json рескрейпа")
    parser.add_argument("--dry-run", action="store_true", help="Показать текст без отправки")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    send_daily_report(args.scope, args.summary, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
