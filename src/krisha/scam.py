"""Скам-детектор: бейдж «подозрительно дёшево» (задача 4 бэклога).

Классика мошенничества на krisha — цена заметно ниже рынка, чтобы собрать
задатки/предоплаты. Модель уже даёт справедливую цену, поэтому главный
сигнал — отклонение вниз от оценки; к нему добавляем простые эвристики
по самому объявлению. Балльная система, порог → уровень риска.
"""

from __future__ import annotations

import re
from typing import Any

# Слова про предоплату/задаток — главный «крючок» мошенников
_PREPAY_RE = re.compile(
    r"задаток|задатк|предоплат|аванс|бронировани|депозит|каспи\s*перевод|kaspi\s*перевод",
    re.IGNORECASE,
)
_URGENCY_RE = re.compile(r"срочно|сегодня\s+отда|уезжаю|улетаю", re.IGNORECASE)

# Отклонение цены вниз от оценки модели, %
_DEV_STRONG = 30.0
_DEV_MEDIUM = 20.0
_DEV_LIGHT = 12.0

HIGH_THRESHOLD = 5
MEDIUM_THRESHOLD = 3


def assess_scam_risk(
    listing: dict[str, Any], fair_price: float, actual_price: float | None
) -> dict[str, Any] | None:
    """Оценка риска мошенничества. None — подозрительного не нашли.

    Возвращает {"level": "high"|"medium", "score": int, "reasons": [str]}.
    """
    if not actual_price or not fair_price or fair_price <= 0:
        return None

    score = 0
    reasons: list[str] = []

    below_pct = (1 - actual_price / fair_price) * 100
    if below_pct >= _DEV_STRONG:
        score += 3
        reasons.append(f"цена на {below_pct:.0f}% ниже оценки модели")
    elif below_pct >= _DEV_MEDIUM:
        score += 2
        reasons.append(f"цена на {below_pct:.0f}% ниже оценки модели")
    elif below_pct >= _DEV_LIGHT:
        score += 1
        reasons.append(f"цена на {below_pct:.0f}% ниже оценки модели")

    photos = listing.get("photos") or []
    if not photos:
        score += 2
        reasons.append("нет фотографий")
    elif len(photos) < 3:
        score += 1
        reasons.append("подозрительно мало фотографий")

    desc = (listing.get("description") or "").strip()
    if _PREPAY_RE.search(desc):
        score += 2
        reasons.append("в описании упоминается задаток/предоплата")
    if _URGENCY_RE.search(desc):
        score += 1
        reasons.append("давление срочностью в описании")
    if len(desc) < 60:
        score += 1
        reasons.append("пустое или очень короткое описание")

    if score >= HIGH_THRESHOLD:
        level = "high"
    elif score >= MEDIUM_THRESHOLD:
        level = "medium"
    else:
        return None
    # Без ценового сигнала не пугаем: просто лаконичное объявление — не скам
    if below_pct < _DEV_LIGHT:
        return None
    return {"level": level, "score": score, "reasons": reasons}
