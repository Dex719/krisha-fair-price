"""Рассылка алертов о выгодных объявлениях (запускается после рескрейпа).

Использование:
    python scripts/send_alerts.py            # найти и разослать
    python scripts/send_alerts.py --dry-run  # только показать, без отправки

Нужен env TELEGRAM_BOT_TOKEN (в GitHub Actions — секрет).
"""

import argparse
import logging
import sys

from krisha.alerts import find_good_deals, format_alert, match_filters, send_alerts
from krisha.subscriptions import load_subscriptions


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    subs = load_subscriptions()
    if not subs:
        print("Подписчиков нет — рассылать некому")
        return 0

    deals = find_good_deals()
    print(f"Подписчиков: {len(subs)}, новых выгодных лотов: {len(deals)}")
    if not deals:
        return 0

    if args.dry_run:
        for chat_id, flt in subs.items():
            matched = [d for d in deals if match_filters(d, flt)]
            print(f"--- chat {chat_id}: {len(matched)} лотов")
            if matched:
                print(format_alert(matched[:5]))
        return 0

    sent = send_alerts(subs, deals)
    print(f"Отправлено сообщений: {sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
