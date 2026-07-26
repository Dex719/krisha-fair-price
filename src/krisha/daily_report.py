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


# issue #154: сколько проходов подряд должна расти очередь деталей, чтобы это
# считалось проблемой. Один рост — обычный день с большим притоком; три подряд
# означают, что потолок докачки ниже притока и отставание копится.
QUEUE_GROWTH_STREAK = 3
# Сколько проходов подряд докачка может упираться в свой потолок, прежде чем
# это станет сигналом. Ровно эта слепота прятала отставание: двенадцать дней
# подряд «докачано 1000» читалось как успех, а означало «упёрлись».
CAP_HIT_STREAK = 3
MAX_RETRAIN_AGE_DAYS = 8


def _invariants(stats: dict | None, db_path: Path, scope: str) -> list[tuple[bool, str]]:
    """Проверки «конвейер жив». [(ок, текст), ...] — порядок фиксирован.

    Смысл в том, чтобы отчёт начинался с вердикта, а не с таблицы цифр.
    Поломку 14.07 не заметили не из-за отсутствия алертов, а потому что
    читать надо было цифры и сравнивать их с ожиданием в голове.
    """
    from krisha.db import recent_sweep_runs

    checks: list[tuple[bool, str]] = []
    if not stats:
        return [(False, "счётчики прохода не найдены — рескрейп не доработал")]

    # Формулировки нейтральные, а не «всё хорошо»: строка показывается
    # ТОЛЬКО когда проверка провалилась, и «выдача отдаёт объявления (0)»
    # читалось бы как утверждение об успехе рядом с красным заголовком.
    found = stats.get("found_in_search") or 0
    checks.append((found > 0, f"в выдаче объявлений: {found}"))

    discovered = stats.get("discovered_new")
    if discovered is not None:
        checks.append((discovered > 0, f"новых лотов найдено: {discovered}"))

    fetched = stats.get("details_fetched")
    if fetched is not None:
        checks.append((fetched > 0, f"деталей докачано: {fetched}"))

    if stats.get("failed_shards"):
        checks.append((False, f"шардов не покрыто: {len(stats['failed_shards'])}"))
    if stats.get("banned"):
        checks.append((False, f"бан на фазе «{stats.get('banned_phase') or '?'}»"))
    if stats.get("time_budget_hit"):
        checks.append((False, "проход упёрся в бюджет времени"))
    if stats.get("suspicious"):
        checks.append((False, "parse-rate просел против базлайна"))

    deal = "arenda" if scope == "rent" else "prodazha"
    runs = recent_sweep_runs(limit=max(QUEUE_GROWTH_STREAK, CAP_HIT_STREAK) + 1,
                            deal=deal, db_path=db_path)
    queues = [r["detail_queue_after"] for r in runs if r["detail_queue_after"] is not None]
    if len(queues) > QUEUE_GROWTH_STREAK:
        # runs идут свежими первыми, поэтому рост во времени — это убывание
        # по списку: queues[0] (сегодня) больше queues[1] (вчера) и т.д.
        growing = all(
            queues[i] > queues[i + 1] for i in range(QUEUE_GROWTH_STREAK)
        )
        if growing:
            checks.append((
                False,
                f"очередь деталей растёт {QUEUE_GROWTH_STREAK} прохода подряд "
                f"({queues[QUEUE_GROWTH_STREAK]} → {queues[0]})",
            ))
        else:
            checks.append((True, f"очередь деталей: {queues[0]}"))

    capped = [
        r for r in runs[:CAP_HIT_STREAK]
        if r["max_new_details"] and r["details_fetched"] == r["max_new_details"]
    ]
    if len(capped) >= CAP_HIT_STREAK:
        checks.append((
            False,
            f"докачка упирается в потолок {capped[0]['max_new_details']} "
            f"{len(capped)} прохода подряд — сбор ограничен лимитом, не рынком",
        ))

    if scope == "sale":
        meta = _load_json(MODEL_META_PATH)
        trained = (meta or {}).get("trained_at")
        if trained:
            try:
                age = (datetime.now(ALMATY_TZ) - datetime.fromisoformat(trained).replace(
                    tzinfo=datetime.fromisoformat(trained).tzinfo or ALMATY_TZ
                )).days
                checks.append((
                    age <= MAX_RETRAIN_AGE_DAYS,
                    f"модель обучена {age} дн. назад",
                ))
            except ValueError:
                pass
    return checks


def _verdict_lines(stats: dict | None, db_path: Path, scope: str) -> list[str]:
    checks = _invariants(stats, db_path, scope)
    failed = [text for ok, text in checks if not ok]
    if not failed:
        return [f"✅ <b>ОК</b> — все проверки прошли ({len(checks)})"]
    return [
        f"🔴 <b>ПРОБЛЕМА</b> — не прошло проверок: {len(failed)} из {len(checks)}",
        *(f"  • {t}" for t in failed),
    ]


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
    # issue #154: вердикт первым, до всех цифр. Поломку 14.07 не заметили не
    # из-за отсутствия алертов, а потому что отчёт требовал читать таблицу и
    # сравнивать её с ожиданием в голове — а глазу «новых 1000» выглядело
    # ровно так же, как в здоровый день.
    lines.extend(_verdict_lines(stats, db_path, scope))
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
        # issue #156: без этой строки восстановительный проход в отчёте
        # неотличим от удачного дня на рынке — «новых 20000» читается как
        # рекордный приток, хотя это то, что мы пропустили за перерыв.
        if stats.get("recovery_pass"):
            lines.append(
                f"📌 Восстановительный проход: лоты не наблюдались "
                f"{stats.get('observation_gap_days', '?')} дн. "
                "«Новые» — в основном пропущенное за перерыв, а не приток рынка; "
                "снятия датированы сегодня, хотя лоты ушли внутри перерыва"
            )
        if stats.get("failed_shards"):
            lines.append("⚠️ Сбойные шарды: " + ", ".join(stats["failed_shards"]))
        if stats.get("delist_blocked"):
            share = stats.get("delist_share")
            share_txt = f"{share * 100:.0f}%" if isinstance(share, (int, float)) else "?"
            lines.append(
                f"🛑 Массовое снятие заблокировано: кандидатов {share_txt} от активных. "
                "Снятия НЕ проставлены — проверьте, отдаёт ли выдача полные страницы"
            )
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
