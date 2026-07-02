"""Подписки на алерты о выгодных объявлениях.

Хранение: data/subscriptions.json. Файл живёт в git-репозитории — это
единственное бесплатное персистентное хранилище в нашей схеме (диск на
хостинге стирается при каждом деплое). Поэтому при изменении подписки
приложение коммитит файл в GitHub через Contents API (нужен env GITHUB_PAT
с правом contents:write; без него подписки живут до ближайшего деплоя).

Формат: {"<chat_id>": {"rooms": 2|null, "max_price": 45000000|null,
"district": "Bostandykskiy_r-n"|null, "since": iso}}
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from krisha.config import DATA_DIR
from krisha.stats import DISTRICT_RU

logger = logging.getLogger(__name__)

SUBSCRIPTIONS_PATH = DATA_DIR / "subscriptions.json"
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Dex719/krisha-fair-price")
_GH_API = "https://api.github.com"

# «2к», «2-к», «2комн» и просто «2» → комнаты; «45млн», «до 45» → бюджет
_ROOMS_RE = re.compile(r"^([1-6])(?:-?к(?:омн\w*)?)?$", re.IGNORECASE)
_PRICE_RE = re.compile(r"^(\d{1,4}(?:[.,]\d+)?)(?:млн)?$", re.IGNORECASE)


def load_subscriptions() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(SUBSCRIPTIONS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def parse_filters(text: str) -> dict[str, Any]:
    """Разбирает хвост команды /alerts_on: комнаты, бюджет (млн ₸), район.

    Числа 1–6 (или «2к») — комнаты; числа ≥7 (или «45млн») — максимум цены
    в млн ₸; слово — район (по подстроке русского названия).
    """
    flt: dict[str, Any] = {"rooms": None, "max_price": None, "district": None}
    for token in re.split(r"[\s,]+", text.strip()):
        if not token or token.lower() in ("до",):
            continue
        m = _ROOMS_RE.match(token)
        if m and flt["rooms"] is None:
            flt["rooms"] = int(m.group(1))
            continue
        m = _PRICE_RE.match(token)
        if m:
            value = float(m.group(1).replace(",", "."))
            if value >= 7:  # млн ₸; меньшие числа — это были бы комнаты
                flt["max_price"] = int(value * 1_000_000)
                continue
        low = token.lower().strip(".")
        for slug, ru in DISTRICT_RU.items():
            if low in ru.lower() or ru.lower().startswith(low):
                flt["district"] = slug
                break
    return flt


def describe_filters(flt: dict[str, Any]) -> str:
    parts = []
    if flt.get("rooms"):
        parts.append(f"{flt['rooms']}-комн")
    if flt.get("max_price"):
        parts.append(f"до {flt['max_price'] / 1_000_000:g} млн ₸")
    if flt.get("district"):
        parts.append(DISTRICT_RU.get(flt["district"], flt["district"]))
    return ", ".join(parts) if parts else "без фильтров (все выгодные)"


def set_subscription(chat_id: int, flt: dict[str, Any]) -> None:
    subs = load_subscriptions()
    subs[str(chat_id)] = {**flt, "since": datetime.now(timezone.utc).isoformat()}
    _save(subs, f"alerts: подписка {chat_id}")


def remove_subscription(chat_id: int) -> bool:
    subs = load_subscriptions()
    if str(chat_id) not in subs:
        return False
    del subs[str(chat_id)]
    _save(subs, f"alerts: отписка {chat_id}")
    return True


def _save(subs: dict[str, Any], message: str) -> None:
    payload = json.dumps(subs, ensure_ascii=False, indent=2, sort_keys=True)
    SUBSCRIPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUBSCRIPTIONS_PATH.write_text(payload)
    _push_to_github(payload, message)


def _push_to_github(payload: str, message: str) -> None:
    """Коммитит subscriptions.json в GitHub, чтобы пережить редеплой."""
    token = os.environ.get("GITHUB_PAT")
    if not token:
        logger.warning("GITHUB_PAT не задан — подписка сохранена только локально")
        return
    url = f"{_GH_API}/repos/{GITHUB_REPO}/contents/data/subscriptions.json"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        resp = httpx.get(url, headers=headers, timeout=15.0)
        sha = resp.json().get("sha") if resp.status_code == 200 else None
        body = {
            "message": message,
            "content": base64.b64encode(payload.encode()).decode(),
            **({"sha": sha} if sha else {}),
        }
        put = httpx.put(url, headers=headers, json=body, timeout=15.0)
        if put.status_code not in (200, 201):
            logger.warning("GitHub push подписок: %s %s", put.status_code, put.text[:200])
    except httpx.HTTPError as exc:
        logger.warning("GitHub push подписок не удался: %s", exc)
