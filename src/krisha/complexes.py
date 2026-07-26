"""Справочник ЖК: нормализация имён, снапшот для деплоя, лукап для фичей.

Джойн listings ↔ complexes идёт по нормализованному имени: у объявления это
`raw_params["map.complex"]` (заполнен у ~58% объявлений) или `complex_name`,
у ЖК — `name` со страницы каталога. Названия пишут по-разному («ЖК "Хан Тенгри"»
vs «Хан Тенгри»), поэтому регистр/кавычки/префиксы срезаем.

Продакшен-деплой живёт без БД, поэтому справочник снапшотится в
models/complexes.json (как stats.json) и коммитится вместе с моделью.
"""

import json
import logging
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from krisha.config import COMPLEXES_SNAPSHOT_PATH, DB_PATH

logger = logging.getLogger(__name__)

_PREFIX_RE = re.compile(
    r"^(жк|жилой комплекс|коттеджный городок|кг|мкр|клубный дом)[\s.:]+", re.I
)
_JUNK_RE = re.compile(r"[\"'«»“”()\[\]]")
_SPACE_RE = re.compile(r"[\s._\-—–/]+")

# Поля справочника, которые подмешиваются к объявлению (фичи + карточка «О доме»)
COMPLEX_ATTRS = [
    "developer", "housing_class", "completion_year", "construction_status",
    "material", "max_floors", "apartments_count",
]


def normalize_complex_name(name: str | None) -> str:
    """«ЖК "Хан-Тенгри"» → «хан тенгри». Пустое/мусор → ""."""
    if not name:
        return ""
    text = _JUNK_RE.sub("", str(name).strip().lower())
    text = _PREFIX_RE.sub("", text).strip()
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


_PHASE_RE = re.compile(r"\s+(?:\d+(?:\s+\d+)?|[ivx]+)$")


def lookup_complex_attrs(name: str | None, lookup: dict[str, dict]) -> dict:
    """Атрибуты ЖК по имени: точное совпадение, иначе без номера очереди.

    «Alma City 5» и «Alatau City 2.0» в объявлениях — очереди одного ЖК,
    в каталоге же одна страница «Alma City»: срезаем хвостовой номер.
    """
    key = normalize_complex_name(name)
    if not key:
        return {}
    if key in lookup:
        return lookup[key]
    return lookup.get(_PHASE_RE.sub("", key), {})


def snapshot_complexes(db_path: Path | str = DB_PATH,
                       out_path: Path | str = COMPLEXES_SNAPSHOT_PATH) -> int:
    """Таблица complexes → models/complexes.json: {name_norm: {attrs}}.

    При коллизии имён выигрывает запись с большим числом заполненных полей.
    Возвращает число записей в снапшоте.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM complexes").fetchall()
    conn.close()

    lookup: dict[str, dict] = {}
    for row in rows:
        key = normalize_complex_name(row["name"])
        if not key:
            continue
        attrs = {a: row[a] for a in COMPLEX_ATTRS}
        filled = sum(v is not None for v in attrs.values())
        old = lookup.get(key)
        if old is None or filled > sum(v is not None for v in old.values()):
            lookup[key] = attrs

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(lookup, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    logger.info("Снапшот ЖК: %s записей → %s", len(lookup), out_path)
    return len(lookup)


@lru_cache(maxsize=1)
def load_complex_lookup(path: str | None = None) -> dict[str, dict]:
    """Лукап name_norm → attrs из снапшота. Нет файла → пустой dict (фичи = unknown)."""
    p = Path(path) if path else COMPLEXES_SNAPSHOT_PATH
    if not p.exists():
        logger.warning("Снапшот ЖК не найден (%s) — фичи ЖК будут пустыми", p)
        return {}
    return json.loads(p.read_text(encoding="utf-8"))
