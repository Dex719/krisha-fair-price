"""Этап 5: LLM-анализ текста описания — red flags и плюсы (Gemini Flash).

Описание объявления прогоняется через Gemini с жёсткой JSON-схемой и
фиксированным словарём флагов. Результат кэшируется в таблице `llm_flags`
(ключ — id объявления + хэш описания), так что каждый текст анализируется
один раз. Без ключа `GEMINI_API_KEY` всё деградирует мягко: возвращаем
только то, что уже в кэше.

Запуск пакетного анализа: `python scripts/analyze_flags.py`.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
from typing import Any

import httpx

from krisha.config import DB_PATH
from krisha.db import get_conn

logger = logging.getLogger(__name__)

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
# flash-lite: у бесплатного тарифа лимит ~1000 запросов/день (у 2.5-flash — всего 20)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 4
DESC_MAX_CHARS = 1500  # длиннее почти не бывает, а хвост — шаблонный текст агентств

# --- Словарь флагов: ключ → (вид, подпись для бейджа) ---------------------
# Вид: "warn" — настораживает покупателя, "plus" — скрытый плюс из текста.
FLAGS_RU: dict[str, tuple[str, str]] = {
    # red flags
    "urgent_sale": ("warn", "Срочная продажа"),
    "pledge": ("warn", "В залоге / ипотеке"),
    "power_of_attorney": ("warn", "Продажа по доверенности"),
    "docs_issues": ("warn", "Вопросы с документами"),
    "tenants": ("warn", "Сдана арендаторам"),
    "co_owners": ("warn", "Несколько собственников / доля"),
    "needs_repair": ("warn", "Требует ремонта"),
    "exchange": ("warn", "Рассматривают обмен"),
    # плюсы
    "bargain": ("plus", "Торг уместен"),
    "windows_courtyard": ("plus", "Окна во двор"),
    "warm": ("plus", "Тёплая квартира"),
    "new_plumbing": ("plus", "Новая сантехника / коммуникации"),
    "quiet_area": ("plus", "Тихий двор / район"),
    "good_infrastructure": ("plus", "Развитая инфраструктура"),
    "furniture_stays": ("plus", "Мебель и техника остаются"),
    "good_view": ("plus", "Хороший вид из окон"),
}

_FLAG_KEYS = list(FLAGS_RU)

PROMPT = """Ты анализируешь тексты объявлений о продаже квартир в Алматы (krisha.kz).
Для каждого объявления выбери подходящие флаги СТРОГО из списка ключей:
{keys}

Правила:
- Флаг ставь только если он явно следует из текста, не выдумывай.
- "bargain" — только если торг прямо допускается ("торг", "торг уместен");
  "без торга" — это НЕ bargain.
- "pledge" — квартира в залоге/ипотеке у банка; обычная фраза "подходит под
  ипотеку" — это НЕ pledge.
- "needs_repair" — текст говорит о черновой отделке или необходимости ремонта.
- "windows_courtyard" — только при явной фразе про окна во двор ("окна выходят
  во двор"); "пластиковые окна" или "тихий двор" сами по себе — это НЕ оно.
- "good_view" — только про явный вид из окон (горы, панорама), не "светлая".
- Пустой список флагов — нормальный ответ.

Объявления (id и текст):
{items}"""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "flags": {
                "type": "ARRAY",
                "items": {"type": "STRING", "enum": _FLAG_KEYS},
            },
        },
        "required": ["id", "flags"],
    },
}

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_flags (
    listing_id  INTEGER PRIMARY KEY,
    desc_hash   TEXT NOT NULL,
    model       TEXT,
    flags       TEXT NOT NULL,                 -- JSON-массив ключей из FLAGS_RU
    analyzed_at TEXT DEFAULT (datetime('now'))
);
"""


def desc_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.executescript(CACHE_SCHEMA)


def get_cached_flags(listing_id: int, text: str) -> list[str] | None:
    """Флаги из кэша; None — не анализировали (или описание изменилось)."""
    try:
        with get_conn(DB_PATH) as conn:
            _ensure_cache_table(conn)
            row = conn.execute(
                "SELECT desc_hash, flags FROM llm_flags WHERE listing_id = ?",
                (int(listing_id),),
            ).fetchone()
    except (sqlite3.OperationalError, FileNotFoundError):
        return None
    if row is None or row["desc_hash"] != desc_hash(text):
        return None
    try:
        flags = json.loads(row["flags"])
    except json.JSONDecodeError:
        return None
    return [f for f in flags if f in FLAGS_RU]


def save_flags(listing_id: int, text: str, flags: list[str]) -> None:
    with get_conn(DB_PATH) as conn:
        _ensure_cache_table(conn)
        conn.execute(
            "INSERT INTO llm_flags (listing_id, desc_hash, model, flags) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(listing_id) DO UPDATE SET desc_hash = excluded.desc_hash, "
            "model = excluded.model, flags = excluded.flags, "
            "analyzed_at = datetime('now')",
            (int(listing_id), desc_hash(text), GEMINI_MODEL, json.dumps(flags)),
        )
        conn.commit()


def _parse_response(payload: dict[str, Any]) -> dict[int, list[str]]:
    """Достаёт {listing_id: [flags]} из ответа Gemini. Бракует мусор молча."""
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        rows = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        logger.warning("Gemini: не удалось разобрать ответ")
        return {}
    out: dict[int, list[str]] = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            lid = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        flags = [f for f in row.get("flags", []) if f in FLAGS_RU]
        out[lid] = sorted(set(flags), key=_FLAG_KEYS.index)
    return out


def analyze_batch(
    items: list[tuple[int, str]], api_key: str | None = None
) -> dict[int, list[str]] | None:
    """Один запрос к Gemini на пачку описаний. None — нет ключа или ошибка."""
    api_key = api_key or os.environ.get(GEMINI_API_KEY_ENV)
    if not api_key or not items:
        return None
    blocks = "\n\n".join(
        f"[id={lid}]\n{text.strip()[:DESC_MAX_CHARS]}" for lid, text in items
    )
    body = {
        "contents": [
            {"parts": [{"text": PROMPT.format(keys=", ".join(_FLAG_KEYS), items=blocks)}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0.0,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = GEMINI_URL.format(model=GEMINI_MODEL)
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT)
        except httpx.HTTPError as exc:
            logger.warning("Gemini: сетевая ошибка (%s), попытка %d", exc, attempt)
            time.sleep(2 * attempt)
            continue
        if resp.status_code == 200:
            return _parse_response(resp.json())
        if resp.status_code in (429, 500, 503):
            wait = min(60.0, 5.0 * 2 ** (attempt - 1))
            logger.warning("Gemini: HTTP %d, ждём %.0fs", resp.status_code, wait)
            time.sleep(wait)
            continue
        logger.error("Gemini: HTTP %d — %s", resp.status_code, resp.text[:300])
        return None
    return None


def analyze_one(listing_id: int, text: str, api_key: str | None = None) -> list[str] | None:
    res = analyze_batch([(int(listing_id), text)], api_key=api_key)
    if res is None:
        return None
    return res.get(int(listing_id), [])


def build_text_flags(listing: dict[str, Any], live: bool = True) -> list[dict[str, str]]:
    """Бейджи «Анализ описания» для карточки: [{kind, label}, ...].

    Берём из кэша; если нет и есть ключ — один живой запрос (fail-soft).
    """
    listing_id, text = listing.get("id"), listing.get("description")
    if not listing_id or not text or len(text.strip()) < 20:
        return []
    flags = get_cached_flags(listing_id, text)
    if flags is None and live and os.environ.get(GEMINI_API_KEY_ENV):
        flags = analyze_one(listing_id, text)
        if flags is not None:
            try:
                save_flags(listing_id, text, flags)
            except sqlite3.OperationalError:  # read-only FS и т.п. — не страшно
                logger.warning("llm_flags: не удалось сохранить кэш")
    if not flags:
        return []
    return [
        {"kind": FLAGS_RU[f][0], "label": FLAGS_RU[f][1]}
        for f in flags
        if f in FLAGS_RU
    ]
