"""Рассылка алертов после рескрейпа: выгодные лоты + слежка (/track).

Использование:
    python scripts/send_alerts.py            # найти и разослать
    python scripts/send_alerts.py --dry-run  # только показать, без отправки

Нужен env TELEGRAM_BOT_TOKEN (в GitHub Actions — секрет); для сохранения
состояния слежки — GITHUB_PAT или GITHUB_TOKEN с contents:write.
"""

import argparse
import logging
import sys

from krisha.alerts import find_good_deals, format_alert, match_filters, send_alerts
from krisha.subscriptions import load_subscriptions
from krisha.tracking import check_tracked_updates


def _send_deal_alerts(dry_run: bool) -> None:
    subs = load_subscriptions()
    if not subs:
        print("Подписчиков на выгодные лоты нет")
        return

    deals = find_good_deals()
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


def _send_track_alerts(dry_run: bool) -> None:
    from krisha.bot import tg_call

    updates = check_tracked_updates(persist=not dry_run)
    print(f"Обновлений по слежке (/track): {len(updates)}")
    for chat_id, message in updates:
        if dry_run:
            print(f"--- chat {chat_id}:\n{message}")
            continue
        tg_call("sendMessage", chat_id=chat_id, text=message,
                parse_mode="HTML", disable_web_page_preview=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    _send_deal_alerts(args.dry_run)
    _send_track_alerts(args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
