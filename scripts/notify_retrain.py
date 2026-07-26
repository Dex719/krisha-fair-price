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

from krisha.monitoring import (
    dataset_summary,
    format_retrain_report,
    load_metrics_history,
    notify_retrain,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Отчёт о переобучении в Telegram")
    parser.add_argument("old_meta")
    parser.add_argument("new_meta")
    parser.add_argument("--gate", choices=["success", "failure"], default="success")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    old = json.loads(Path(args.old_meta).read_text(encoding="utf-8"))
    new = json.loads(Path(args.new_meta).read_text(encoding="utf-8"))
    gate_passed = args.gate == "success"

    try:
        dataset = dataset_summary()
    except Exception:  # noqa: BLE001
        dataset = None
    print(
        format_retrain_report(
            old, new, gate_passed, history=load_metrics_history(), dataset=dataset
        )
    )
    sent = notify_retrain(old, new, gate_passed)
    print(f"\nTelegram: {'отправлено' if sent else 'пропущено (нет токена/чата)'}")


if __name__ == "__main__":
    main()
