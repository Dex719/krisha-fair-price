#!/usr/bin/env python
"""Telegram-отчёт после еженедельного retrain (мониторинг модели).

Запуск из workflow (шаг с if: always() после гейта):
    python scripts/notify_retrain.py old_meta.json models/model_meta.json --gate success|failure

Без TELEGRAM_BOT_TOKEN / TG_ADMIN_CHAT_ID тихо печатает отчёт в stdout —
workflow не падает.
"""

import argparse
import json
import logging
from pathlib import Path

from krisha.monitoring import format_retrain_report, load_metrics_history, notify_retrain


def main() -> None:
    parser = argparse.ArgumentParser(description="Отчёт о переобучении в Telegram")
    parser.add_argument("old_meta")
    parser.add_argument("new_meta")
    parser.add_argument("--gate", choices=["success", "failure"], default="success")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    old = json.loads(Path(args.old_meta).read_text())
    new = json.loads(Path(args.new_meta).read_text())
    gate_passed = args.gate == "success"

    print(format_retrain_report(old, new, gate_passed, history=load_metrics_history()))
    sent = notify_retrain(old, new, gate_passed)
    print(f"\nTelegram: {'отправлено' if sent else 'пропущено (нет токена/чата)'}")


if __name__ == "__main__":
    main()
