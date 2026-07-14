"""Извлечение инлайн-JSON вида `window.<name> = {...};` из HTML Krisha.kz.

Общий helper для `detail_parser.py` и `complex_parser.py` (issue #100):
раньше оба модуля держали собственный `re.compile(r"window\\.data\\s*=\\s*(\\{.*?\\});", re.S)`
и брали ПЕРВОЕ `};` после открывающей скобки. Нежадный `.*?` не умеет считать
глубину скобок — если внутри объекта (вложенный подобъект, строка с
описанием квартиры, инлайн-стиль) встречается собственная подстрока `};`,
захват обрывается на ней, `json.loads` падает на обрубленном фрагменте, и
объявление молча теряется (`logger.warning` → `None`), хотя JSON на странице
целый.

Фикс: искать только начало (`window.<name> = `), затем отдать `json.loads`
искать конец через `json.JSONDecoder.raw_decode` — он сам считает глубину
`{}` и учитывает строковые литералы/экранирование, так что `};` внутри
строки или вложенного объекта не обрывает парсинг раньше времени.

Только stdlib (re + json) — модуль не тянет тяжёлые зависимости, безопасно
импортировать и из лёгкого прод-serving пути.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _start_re(var_name: str) -> re.Pattern[str]:
    return re.compile(r"window\." + re.escape(var_name) + r"\s*=\s*")


def extract_window_json(html: str, var_name: str = "data") -> Any | None:
    """Парсит `window.<var_name> = {...};` из HTML. `None`, если не нашли/не распарсили.

    В отличие от старого `WINDOW_DATA_RE.search(...).group(1)` + `json.loads`,
    здесь конец объекта ищет `json.JSONDecoder.raw_decode` — корректно
    проходит вложенные `{}` и строки с `};` внутри, не обрывается на первом
    совпадении.
    """
    m = _start_re(var_name).search(html)
    if m is None:
        return None
    brace_pos = html.find("{", m.end())
    if brace_pos == -1:
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(html, brace_pos)
    except json.JSONDecodeError:
        return None
    return obj
