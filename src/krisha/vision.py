"""Оценка ремонта по фото объявления (задача 10 бэклога, Gemini Vision).

Первые фото объявления скачиваются и уходят в Gemini с жёсткой JSON-схемой:
уровень ремонта + короткий комментарий. Результат кэшируется в таблице
`vision_renovation` (ключ — id объявления + хэш списка фото), так что каждое
объявление анализируется один раз. Без `GEMINI_API_KEY` или без фото — None,
всё остальное работает как раньше.

Оценка не влияет на предсказание цены — это отдельный бейдж для покупателя
(модель фото не видит, поэтому «евроремонт» в дорогой оценке уже частично
сидит через год постройки/ЖК, а бейдж отвечает на вопрос «что там внутри»).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sqlite3
from typing import Any

import httpx

from krisha.config import DB_PATH
from krisha.db import get_conn
from krisha.llm_flags import (
    GEMINI_API_KEY_ENV,
    GEMINI_MODEL,
    GEMINI_URL,
    REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)

MAX_PHOTOS = 3
PHOTO_TIMEOUT = 10.0
MAX_PHOTO_BYTES = 4_000_000

LEVELS_RU = {
    "rough": "черновая отделка",
    "needs_repair": "требует ремонта",
    "dated": "жилой, но устаревший",
    "good": "хороший ремонт",
    "premium": "свежий евроремонт",
}

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "level": {"type": "STRING", "enum": list(LEVELS_RU)},
        "comment": {"type": "STRING"},
    },
    "required": ["level", "comment"],
}

PROMPT = (
    "Ты оцениваешь состояние ремонта квартиры в Алматы по фото из объявления.\n"
    "Выбери level строго из: rough (черновая/предчистовая отделка), "
    "needs_repair (нужен ремонт: старая отделка в плохом состоянии), "
    "dated (жилой, но устаревший ремонт), good (аккуратный современный ремонт), "
    "premium (свежий дорогой ремонт).\n"
    "comment — одна короткая фраза по-русски о том, что видно на фото "
    "(состояние кухни/санузла/полов), без воды.\n"
    "Если на фото только фасад/двор/планировка и интерьера не видно — "
    "level=dated и comment «интерьер на фото не показан»."
)

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS vision_renovation (
    listing_id  INTEGER PRIMARY KEY,
    photos_hash TEXT NOT NULL,
    model       TEXT,
    level       TEXT NOT NULL,
    comment     TEXT,
    analyzed_at TEXT DEFAULT (datetime('now'))
);
"""


def _photos_hash(photos: list[str]) -> str:
    return hashlib.sha256("|".join(photos[:MAX_PHOTOS]).encode()).hexdigest()[:16]


def get_cached(listing_id: int, photos: list[str]) -> dict[str, str] | None:
    try:
        with get_conn(DB_PATH) as conn:
            conn.executescript(CACHE_SCHEMA)
            row = conn.execute(
                "SELECT photos_hash, level, comment FROM vision_renovation "
                "WHERE listing_id = ?",
                (int(listing_id),),
            ).fetchone()
    except (sqlite3.OperationalError, FileNotFoundError):
        return None
    if row is None or row["photos_hash"] != _photos_hash(photos):
        return None
    if row["level"] not in LEVELS_RU:
        return None
    return {"level": row["level"], "label": LEVELS_RU[row["level"]],
            "comment": row["comment"] or ""}


def save_cache(listing_id: int, photos: list[str], level: str, comment: str) -> None:
    try:
        with get_conn(DB_PATH) as conn:
            conn.executescript(CACHE_SCHEMA)
            conn.execute(
                "INSERT INTO vision_renovation (listing_id, photos_hash, model, level, comment) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(listing_id) DO UPDATE SET photos_hash = excluded.photos_hash, "
                "model = excluded.model, level = excluded.level, "
                "comment = excluded.comment, analyzed_at = datetime('now')",
                (int(listing_id), _photos_hash(photos), GEMINI_MODEL, level, comment),
            )
            conn.commit()
    except (sqlite3.OperationalError, FileNotFoundError):
        logger.warning("vision: не удалось сохранить кэш для %s", listing_id)


def _download_photos(photos: list[str]) -> list[tuple[str, bytes]]:
    """Скачивает до MAX_PHOTOS фото; битые молча пропускаем."""
    out: list[tuple[str, bytes]] = []
    for url in photos[:MAX_PHOTOS]:
        if not url.startswith("https://"):
            continue
        try:
            resp = httpx.get(url, timeout=PHOTO_TIMEOUT, follow_redirects=True)
        except httpx.HTTPError:
            continue
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        if resp.status_code != 200 or not ctype.startswith("image/"):
            continue
        if len(resp.content) > MAX_PHOTO_BYTES:
            continue
        out.append((ctype, resp.content))
    return out


def _gemini_assess(images: list[tuple[str, bytes]], api_key: str) -> dict | None:
    parts: list[dict[str, Any]] = [{"text": PROMPT}]
    for ctype, blob in images:
        parts.append({"inline_data": {
            "mime_type": ctype, "data": base64.b64encode(blob).decode()}})
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0.0,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = GEMINI_URL.format(model=GEMINI_MODEL)
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("vision: сетевая ошибка Gemini: %s", exc)
        return None
    if resp.status_code != 200:
        logger.warning("vision: Gemini HTTP %d — %s", resp.status_code, resp.text[:200])
        return None
    try:
        parsed = json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        logger.warning("vision: не удалось разобрать ответ Gemini")
        return None
    if parsed.get("level") not in LEVELS_RU:
        return None
    return parsed


def assess_renovation(listing: dict[str, Any], live: bool = True) -> dict[str, str] | None:
    """Оценка ремонта по фото: кэш, при live=True — живой запрос к Gemini."""
    listing_id = listing.get("id")
    photos = [p for p in (listing.get("photos") or []) if isinstance(p, str)]
    if not listing_id or not photos:
        return None

    cached = get_cached(listing_id, photos)
    if cached is not None:
        return cached
    if not live:
        return None
    api_key = os.environ.get(GEMINI_API_KEY_ENV)
    if not api_key:
        return None

    images = _download_photos(photos)
    if not images:
        return None
    parsed = _gemini_assess(images, api_key)
    if parsed is None:
        return None
    comment = (parsed.get("comment") or "").strip()[:200]
    save_cache(listing_id, photos, parsed["level"], comment)
    return {"level": parsed["level"], "label": LEVELS_RU[parsed["level"]],
            "comment": comment}
