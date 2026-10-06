"""Оценка по вставленному тексту объявления (задача 9 бэклога).

Пользователь кидает боту не ссылку, а сырой текст («Продам 2-комн 60 м²
в Бостандыкском районе…») — Gemini со строгой JSON-схемой достаёт параметры
квартиры, из них собирается listing-dict и уходит в обычный
`predict_from_listing`. Без `GEMINI_API_KEY` функция мягко возвращает None,
бот отвечает прежней подсказкой про ссылку.

Ответ Gemini кэшируется в памяти процесса (ключ — sha256 нормализованного
текста): живой вызов стоит дневной квоты ключа, а без кэша любой участник
чата мог выжечь её, слав боту один и тот же длинный текст по кругу.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import httpx

from krisha.api.cache import TTLCache
from krisha.llm_flags import (
    GEMINI_API_KEY_ENV,
    GEMINI_MODEL,
    GEMINI_URL,
    REQUEST_TIMEOUT,
)
from krisha.stats import DISTRICT_RU

logger = logging.getLogger(__name__)

MIN_TEXT_LEN = 40  # короче — вряд ли описание квартиры
_RU_TO_SLUG = {ru.lower(): slug for slug, ru in DISTRICT_RU.items()}

# Кэш разбора Gemini. Ключ — sha256 нормализованного текста, а не сам текст:
# /privacy обещает «сам текст мы не сохраняем», и кэш в памяти этого не нарушает
# (в значении — только извлечённые параметры, без описания). Час — достаточно,
# чтобы повторная отправка того же текста (или та же вставка из поста в
# нескольких чатах) не тратила квоту, и мало, чтобы не жить дольше самих цен.
# Потолок в 512 записей вытесняет самые старые (см. TTLCache).
#
# Кэш в памяти процесса: при WEB_CONCURRENCY=2 у каждого воркера свой, то есть
# один и тот же текст может один раз сходить в Gemini из каждого воркера.
PARSE_CACHE_TTL_S = 3600.0
PARSE_CACHE_MAXSIZE = 512
# Используем peek/set, а не get_or_call: он держит per-key lock и не чистит
# таблицу замков для ключей, чей producer упал, — а ключ тут задаёт пользователь.
# Single-flight взамен не нужен: частоту живых вызовов душит лимитер бота.
_parse_cache = TTLCache(PARSE_CACHE_TTL_S, maxsize=PARSE_CACHE_MAXSIZE)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_listing": {"type": "BOOLEAN"},
        "rooms": {"type": "INTEGER", "nullable": True},
        "area": {"type": "NUMBER", "nullable": True},
        "floor": {"type": "INTEGER", "nullable": True},
        "total_floors": {"type": "INTEGER", "nullable": True},
        "year_built": {"type": "INTEGER", "nullable": True},
        "ceiling": {"type": "NUMBER", "nullable": True},
        "district": {
            "type": "STRING",
            "nullable": True,
            "enum": [ru for ru in DISTRICT_RU.values()],
        },
        "microdistrict": {"type": "STRING", "nullable": True},
        "building_type": {
            "type": "STRING",
            "nullable": True,
            "enum": ["кирпичный", "панельный", "монолитный", "иное"],
        },
        "complex_name": {"type": "STRING", "nullable": True},
        "price": {"type": "NUMBER", "nullable": True},
        "address": {"type": "STRING", "nullable": True},
    },
    "required": ["is_listing"],
}

PROMPT = """Ты извлекаешь параметры квартиры из текста объявления о продаже в Алматы.

Правила:
- is_listing=false, если текст вообще не похож на объявление о продаже квартиры.
- Заполняй только то, что явно есть в тексте; чего нет — null. Не выдумывай.
- area — общая площадь в м². «60/40/9» — это общая/жилая/кухня, бери 60.
- «5/9 этаж» — floor=5, total_floors=9.
- price — цена в тенге числом: «45 млн» → 45000000, «45 500 000 ₸» → 45500000.
  Цену за м² («450 тыс за квадрат») в price НЕ пиши.
- district — только район Алматы из списка схемы, если он назван или очевиден
  из микрорайона; иначе null.
- building_type: кирпичный/панельный/монолитный/иное — только если сказано.

Текст объявления:
{text}"""


def _gemini_extract(text: str, api_key: str) -> dict[str, Any] | None:
    body = {
        "contents": [{"parts": [{"text": PROMPT.format(text=text.strip()[:3000])}]}],
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
        logger.warning("text_parse: сетевая ошибка Gemini: %s", exc)
        return None
    if resp.status_code != 200:
        logger.warning("text_parse: Gemini HTTP %d — %s", resp.status_code, resp.text[:200])
        return None
    try:
        return json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        logger.warning("text_parse: не удалось разобрать ответ Gemini")
        return None


def _cache_key(text: str) -> str:
    """sha256 нормализованного текста: strip, схлопнутые пробелы, первые 3000 символов.

    Нормализация — чтобы вставка с другими переводами строк и лишними пробелами
    (копирование из мессенджера, из разных вкладок) не плодила новые вызовы.
    Те же 3000 символов уходят в Gemini (`_gemini_extract`), так что хвост за
    ними на ответ не влияет и в ключ не входит.
    """
    normalized = " ".join(text.split())[:3000]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _extract_cached(text: str, api_key: str) -> dict[str, Any] | None:
    """Разбор Gemini через кэш. Кэшируется любой ОТВЕТ модели, в том числе
    «не объявление» (is_listing=false): случайный длинный текст — самый
    дешёвый для злоумышленника способ жечь квоту, и повторять его проще всего.

    Сбой (сеть, квота, непонятный ответ — `_gemini_extract` вернул None) НЕ
    кэшируем: он временный, и человек, повторивший через минуту, не должен
    час получать отказ на тот же текст.
    """
    key = _cache_key(text)
    hit = _parse_cache.peek(key)
    if hit is not None and hit[0] < _parse_cache.ttl:
        return dict(hit[1])
    parsed = _gemini_extract(text, api_key)
    if isinstance(parsed, dict):
        _parse_cache.set(key, dict(parsed))
    return parsed


def parsed_to_listing(parsed: dict[str, Any], text: str) -> dict[str, Any]:
    """Ответ Gemini → listing-dict в формате parse_detail (для фичей)."""
    district_ru = (parsed.get("district") or "").lower()
    listing: dict[str, Any] = {
        "rooms": parsed.get("rooms"),
        "area": parsed.get("area"),
        "floor": parsed.get("floor"),
        "total_floors": parsed.get("total_floors"),
        "year_built": parsed.get("year_built"),
        "ceiling": parsed.get("ceiling"),
        "district": _RU_TO_SLUG.get(district_ru),
        "microdistrict": parsed.get("microdistrict"),
        "building_type": parsed.get("building_type"),
        "complex_name": parsed.get("complex_name"),
        "price": int(parsed["price"]) if parsed.get("price") else None,
        "address_title": parsed.get("address"),
        "description": text.strip(),
        "photos": [],
    }
    return {k: v for k, v in listing.items() if v is not None or k == "price"}


def predict_from_text(text: str) -> dict[str, Any] | None:
    """Полный путь: текст → Gemini → predict. None — нет ключа/не объявление."""
    api_key = os.environ.get(GEMINI_API_KEY_ENV)
    if not api_key or len(text.strip()) < MIN_TEXT_LEN:
        return None
    parsed = _extract_cached(text, api_key)
    if not parsed or not parsed.get("is_listing"):
        return None
    if not parsed.get("area") or not parsed.get("rooms"):
        # без площади и комнатности оценка бессмысленна
        return {"error": "no_key_fields", "parsed": parsed}

    from krisha.predict import predict_from_listing

    listing = parsed_to_listing(parsed, text)
    result = predict_from_listing(listing, live_vision=False)
    result["from_text"] = True
    result["parsed_fields"] = {
        k: v for k, v in listing.items()
        if k not in ("description", "photos") and v is not None
    }
    return result
