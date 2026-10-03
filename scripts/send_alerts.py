"""Рассылка алертов после рескрейпа: выгодные лоты + слежка (/track).

Использование:
    python scripts/send_alerts.py                # найти и разослать
    python scripts/send_alerts.py --dry-run      # только показать, без отправки
    python scripts/send_alerts.py --deal arenda  # только слежка за арендой
                                                 # (после вечернего обхода аренды)

Нужен env TELEGRAM_BOT_TOKEN (в GitHub Actions — секрет); для состояния
(подписчики, слежка) — KRISHA_DB_TOKEN с доступом к приватному репо данных.
Модели должны лежать в models/ (в Actions: python -m krisha.db_release --models).
Опционально TG_CHANNEL_ID (@канал или -100…) — дайджест «Топ-5 лотов дня».
"""

import argparse
import logging
import sys

from krisha.alerts import (
    AlertsPricingFailed,
    find_good_deals,
    format_alert,
    match_filters,
    send_alerts,
)
from krisha.subscriptions import _alert_admin, load_subscriptions
from krisha.tracking import check_tracked_updates


def _send_deal_alerts(dry_run: bool, deals: list) -> None:
    subs = load_subscriptions()
    if not subs:
        print("Подписчиков на выгодные лоты нет")
        return

    print(f"Подписчиков: {len(subs)}, новых выгодных лотов: {len(deals)}")
    if not deals:
        return

    if dry_run:
        for chat_id, flt in subs.items():
            matched = [d for d in deals if match_filters(d, flt)]
            print(f"--- chat {chat_id}: {len(matched)} лотов")
            if matched:
                print(format_alert(matched[:5]))
        return

    sent = send_alerts(subs, deals)
    print(f"Отправлено сообщений: {sent}")


def _quiet_hours(now=None) -> bool:
    """22:00–08:00 по Алматы: алерты приходят без звука (обход аренды кончается за полночь)."""
    from datetime import datetime

    from krisha.usage import ALMATY_TZ

    hour = (now or datetime.now(ALMATY_TZ)).astimezone(ALMATY_TZ).hour
    return hour >= 22 or hour < 8


def _send_track_alerts(dry_run: bool, deal: str = "prodazha") -> None:
    from krisha.bot import tg_call
    from krisha.config import DB_PATH, RENT_DB_PATH

    db_path = RENT_DB_PATH if deal == "arenda" else DB_PATH
    # persist=False: сначала доставляем, потом фиксируем. Раньше состояние
    # сохранялось ДО отправки, и упавший sendMessage (Telegram 5xx, бот
    # заблокирован пользователем) означал потерю алерта навсегда — на
    # следующем проходе сохранённая цена уже равна новой, события нет.
    updates = check_tracked_updates(db_path=db_path, persist=False, deal=deal)
    print(f"Обновлений по слежке (/track, {deal}): {len(updates)}")
    silent = _quiet_hours()
    delivered: set[int] = set()
    for chat_id, message in updates:
        if dry_run:
            print(f"--- chat {chat_id}:\n{message}")
            continue
        resp = tg_call("sendMessage", chat_id=chat_id, text=message,
                       parse_mode="HTML", disable_web_page_preview=True,
                       disable_notification=silent)
        if resp and resp.get("ok"):
            delivered.add(int(chat_id))
        else:
            print(f"Не доставлено в chat {chat_id} — состояние не фиксируем, повторим позже")
    if delivered:
        check_tracked_updates(db_path=db_path, persist=True, only_chats=delivered, deal=deal)


def _post_channel_digest(dry_run: bool, deals: list) -> None:
    from krisha.channel import post_channel_digest

    text = post_channel_digest(deals, dry_run=dry_run)
    if dry_run and text:
        print(f"--- канал:\n{text}")
    else:
        print(f"Пост в канал: {'отправлен' if text else 'пропущен'}")


def _maybe_monthly_report(dry_run: bool) -> None:
    """1-го числа месяца после ночного рескрейпа — отчёт о рынке."""
    from datetime import datetime, timezone

    if datetime.now(timezone.utc).day != 1 and not dry_run:
        return
    from krisha.report import send_monthly_report

    try:
        sent = send_monthly_report(dry_run=dry_run)
    except FileNotFoundError:
        print("Месячный отчёт: базы нет — пропущен")
        return
    print(f"Месячный отчёт: отправлен в {sent} чатов")


def _maybe_weekly_usage_report(dry_run: bool) -> None:
    """По понедельникам (Алматы) — статистика сайта и бота админу."""
    from datetime import datetime

    from krisha.usage import ALMATY_TZ, send_weekly_report

    if datetime.now(ALMATY_TZ).weekday() != 0 and not dry_run:
        return
    sent = send_weekly_report(dry_run=dry_run)
    print(f"Отчёт статистики: {'отправлен' if sent else 'пропущен (нет данных/чата)'}")


def _send_daily_admin_report(dry_run: bool) -> None:
    """Утренняя сводка админу: проход рескрейпа, база, модель, пользователи."""
    from krisha.daily_report import send_daily_report

    sent = send_daily_report(scope="sale", dry_run=dry_run)
    print(f"Утренний отчёт админу: {'отправлен' if sent else 'пропущен'}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--deal", choices=("prodazha", "arenda"), default="prodazha",
                        help="arenda — только слежка за арендой, по базе аренды")
    args = parser.parse_args()

    if args.deal == "arenda":
        _send_track_alerts(args.dry_run, deal="arenda")
        return 0

    try:
        deals, pricing_error = find_good_deals(), None
    except AlertsPricingFailed as exc:
        # Слежка, отчёты и утренний отчёт модели не требуют — шлём их всё равно,
        # а выгодные лоты и дайджест пропускаем: пустой дайджест хуже никакого.
        deals, pricing_error = [], exc
    if pricing_error is None:
        _send_deal_alerts(args.dry_run, deals)
    _send_track_alerts(args.dry_run)
    if pricing_error is None:
        _post_channel_digest(args.dry_run, deals)
    _maybe_monthly_report(args.dry_run)
    _maybe_weekly_usage_report(args.dry_run)
    _send_daily_admin_report(args.dry_run)
    if pricing_error is not None:
        message = f"⚠️ Алерты: {pricing_error}. Выгодные лоты и дайджест канала сегодня не отправлены."
        print(message, file=sys.stderr)
        if not args.dry_run:
            _alert_admin(message)
        return 1  # шаг краснеет — поломку видно в ране, а не только в логах
    return 0


if __name__ == "__main__":
    sys.exit(main())
