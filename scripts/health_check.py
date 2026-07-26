"""Почасовой чек сервера (GitHub Actions health-check.yml) → алерт в Telegram.

Логика: каждый час опрашиваем /api/health. Сообщение шлём только при смене
состояния (упал/ожил/деградировал), чтобы не спамить 24 раза в день; текущее
состояние живёт между запусками в actions/cache (файл --state-file).

Нарочно только stdlib: воркфлоу не ставит зависимости проекта (pip install
тянет catboost на несколько минут ради одного curl).

Env: TELEGRAM_BOT_TOKEN, TG_ADMIN_CHAT_ID; опционально HEALTH_URL.
Выход всегда 0 — красный воркфлоу дублировал бы алерт письмом от GitHub.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.parse
import urllib.request

DEFAULT_URL = "https://dex719-krisha-fair-price.hf.space/api/health"
TIMEOUT = 30
RETRIES = 2  # HF Space может просыпаться — даём вторую попытку

STATUS_RU = {
    "up": "🟢 Сервер работает",
    "degraded": "🟡 Сервер отвечает, но есть проблемы",
    "down": "🔴 Сервер не отвечает",
}


def probe(url: str) -> tuple[str, str]:
    """(status, detail): up / degraded (health не ok) / down (нет ответа)."""
    last_err = ""
    for _ in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
        except Exception as exc:  # noqa: BLE001 — любой сбой сети = down
            last_err = str(exc)
            continue
        problems = [
            f"{key}={data.get(key)!r}"
            for key, good in (("status", "ok"), ("model_loaded", True), ("tg_webhook", "ok"))
            if data.get(key) != good
        ]
        if problems:
            return "degraded", ", ".join(problems)
        return "up", ""
    return "down", last_err


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TG_ADMIN_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN/TG_ADMIN_CHAT_ID не заданы — алерт не отправлен")
        return False
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as exc:  # noqa: BLE001
        print(f"Отправка в Telegram не удалась: {exc}")
        return False


def build_message(status: str, prev: str, detail: str, url: str) -> str:
    lines = [f"<b>{STATUS_RU[status]}</b>"]
    if prev and prev != "unknown":
        lines.append(f"Было: {STATUS_RU.get(prev, prev)}")
    if detail:
        # detail — текст исключения, а сообщение уходит с parse_mode=HTML.
        # У urllib ошибки выглядят как «<urlopen error [Errno 111] ...>»:
        # Telegram видел незакрытый тег, отвечал 400 и алерт о ПАДЕНИИ сервиса
        # просто не доходил — ровно тогда, когда он нужнее всего.
        lines.append(f"Детали: {html.escape(detail)}")
    lines.append(url.removesuffix("/api/health"))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Health-check с алертом при смене состояния")
    parser.add_argument("--state-file", default="health_state.json")
    parser.add_argument("--heartbeat", action="store_true", help="Слать статус всегда")
    args = parser.parse_args()

    url = os.environ.get("HEALTH_URL", DEFAULT_URL)
    status, detail = probe(url)

    prev = "unknown"
    try:
        prev = json.loads(open(args.state_file, encoding="utf-8").read()).get("status", "unknown")
    except (OSError, json.JSONDecodeError):
        pass

    print(f"Статус: {status} (было: {prev}){' — ' + detail if detail else ''}")
    changed = status != prev and prev != "unknown"
    first_bad = prev == "unknown" and status != "up"
    if changed or first_bad or args.heartbeat:
        send_telegram(build_message(status, prev, detail, url))

    with open(args.state_file, "w", encoding="utf-8") as fh:
        json.dump({"status": status}, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
