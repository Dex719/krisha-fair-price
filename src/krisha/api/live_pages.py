"""Живые цифры в разметке страниц и то, что поисковики и нейросети читают без JS.

Страницы несут «снимок» чисел в элементах ``[data-l=…]`` (объявлений в базе,
медиана метра, ошибка модели), который ``site.js`` заменяет ответами
``/api/stats`` и ``/api/health``. Снимок правили руками, и он разъезжался с
данными: в разметке стояло 21 113 объявлений при живых 44 тысячах, ошибка
модели на разных страницах была 7,3% и 7,6%. Человек видел верные числа после
ответа API, а поисковики и нейросети, которые JS не исполняют, цитировали
разметку. Здесь снимок подставляет сервер из тех же данных, что отдаёт API.

Подстановка идёт при старте процесса, после прогрева кэшей. Процесс
перезапускается после каждого сбора данных и переобучения модели, поэтому
«снимок» не старше суток, а форматы совпадают с ``site.js`` до символа:
пришедший ответ API не меняет текст и ничего не мерцает.

Здесь же:

* столбики районов на главной — та же разметка, что рисует её скрипт;
* FAQPage (JSON-LD) из видимых блоков «Частые вопросы» — разметка собирается
  из того же HTML, что видит человек, и не может с ним разойтись;
* сводка рынка в ``<noscript>`` на «Рынке»: числа на странице рисует JS,
  без него оставалась только просьба включить JavaScript;
* вариант «Рынка» для аренды (``/stats?mode=rent``) со своим title,
  description и canonical;
* ``/llms.txt`` — короткая справка о сервисе для нейросетей.
"""

from __future__ import annotations

import html
import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

NB = " "
ALMATY_TZ = timezone(timedelta(hours=5))
TOTAL_FORMS = ("объявление", "объявления", "объявлений")

# ------------------------------------------------------------- форматы site.js


def _group(digits: str) -> str:
    """Разряды через неразрывный пробел — как group() в site.js."""
    return re.sub(r"\B(?=(\d{3})+(?!\d))", NB, digits)


def ru(n: float | int) -> str:
    """Целое с разрядами: 44 026 (site.js: ru)."""
    n = int(Decimal(str(n)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return ("−" if n < 0 else "") + _group(str(abs(n)))


def fmt(n: float, digits: int = 1) -> str:
    """Дробное с запятой: 7,0 (site.js: fmt; toFixed округляет половину вверх)."""
    q = Decimal(1).scaleb(-digits) if digits else Decimal(1)
    s = str(abs(Decimal(str(n))).quantize(q, rounding=ROUND_HALF_UP))
    whole, _, frac = s.partition(".")
    neg = n < 0 and any(c in "123456789" for c in whole + frac)
    return ("−" if neg else "") + _group(whole) + ("," + frac if frac else "")


def pct(n: float, digits: int = 1) -> str:
    return fmt(n, digits) + "%"


def plural(n: int, forms: tuple[str, str, str] | list[str]) -> str:
    a = abs(n) % 100
    b = a % 10
    if 10 < a < 20:
        return forms[2]
    if 1 < b < 5:
        return forms[1]
    return forms[0] if b == 1 else forms[2]


def almaty_date(iso: str | None) -> str | None:
    """«2026-10-05T09:55:43+00:00» → «05.10.2026» по времени Алматы (site.js: almatyDate)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ALMATY_TZ).strftime("%d.%m.%Y")


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


# ------------------------------------------------------------- снимок значений


def live_values(stats: Mapping | None, health: Mapping | None) -> dict[str, str]:
    """Тексты для [data-l=…] — те же ключи и форматы, что в apply() из site.js.

    ``health`` — метрики модели в форме ответа /api/health (без webhook и
    свежести: «обновлено N ч назад» в разметке устарело бы через час).
    Нет поля — нет ключа: элемент остаётся с тем, что лежит в файле.
    """
    out: dict[str, str] = {}
    s = stats or {}
    h = health or {}
    total = _num(s.get("total_listings"))
    if total is not None:
        out["total"] = ru(total)
    ppsm = _num(s.get("median_ppsm"))
    if ppsm is not None:
        out["ppsm"] = ru(ppsm)
        out["ppsmk"] = ru(ppsm / 1000) + NB + "тыс" + NB + "₸/м²"
    price = _num(s.get("median_price"))
    if price is not None:
        out["medprice"] = ru(price)
        out["medpricem"] = fmt(price / 1e6, 1) + NB + "млн" + NB + "₸"
    upd = almaty_date(s.get("updated_at"))
    if upd:
        out["upd"] = upd
    mape = _num(h.get("model_error_pct"))
    if mape is not None:
        out["mape"] = pct(mape)
        # «для квартиры за 40 млн ₸ это около ±N млн ₸» на «О проекте» (там же — скрипт)
        out["x40"] = fmt(mape * 0.4, 1)
    ci = h.get("model_error_ci_pct")
    if isinstance(ci, (list, tuple)) and len(ci) == 2 and all(_num(v) is not None for v in ci):
        out["mapeci"] = ", 95% ДИ " + fmt(ci[0]) + "–" + pct(ci[1])
    mdape = _num(h.get("model_median_error_pct"))
    if mdape is not None:
        out["mdape"] = pct(mdape)
    rmape = _num(h.get("rent_model_error_pct"))
    if rmape is not None:
        out["rmape"] = pct(rmape)
    r2 = _num(h.get("model_r2"))
    if r2 is not None:
        out["r2"] = f"{Decimal(str(r2)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)}"
    mae = _num(h.get("model_mae"))
    if mae is not None:
        out["mae"] = fmt(mae / 1e6, 2)
    return out


# Элемент с data-l и текстом без вложенных тегов: <b data-l="mape" data-count>7,3%</b>
_LIVE_EL_RE = re.compile(
    r'(?P<open><(?P<tag>[a-z][a-z0-9]*)\b[^>]*?\sdata-l="(?P<key>[a-z0-9]+)"[^>]*>)'
    r"(?P<text>[^<]*)(?P<close></(?P=tag)>)"
)
_FORMS_RE = re.compile(r'\sdata-forms="([^"]*)"')


def fill_live(page: str, values: Mapping[str, str], total: int | None = None) -> str:
    """Подставляет значения в [data-l=…]; totalw — слово при числе total в нужной форме."""

    def sub(m: re.Match[str]) -> str:
        key = m.group("key")
        if key == "totalw":
            if total is None:
                return m.group(0)
            forms_m = _FORMS_RE.search(m.group("open"))
            forms = forms_m.group(1).split(",") if forms_m else list(TOTAL_FORMS)
            if len(forms) != 3:
                return m.group(0)
            text = plural(total, forms)
        elif key in values:
            text = values[key]
        else:
            return m.group(0)
        return m.group("open") + html.escape(text, quote=False) + m.group("close")

    return _LIVE_EL_RE.sub(sub, page)


def _js_round(x: float) -> int:
    """Math.round из JS: половина — вверх, к +∞ (round() в Python — к чётному)."""
    return math.floor(x + 0.5)


_BARS_RE = re.compile(
    r'(?P<cols_open><div class="dbars"[^>]*>)(?P<cols>.*?)(?P<mid></div>\s*<div class="dlabels"[^>]*>)'
    r"(?P<labs>.*?)(?P<end></div>\s*</div></section>)",
    flags=re.S,
)


def district_bars(page: str, stats: Mapping | None) -> str:
    """Столбики районов на главной — та же разметка, что рисует draw() в index.html."""
    s = stats or {}
    ds = [d for d in s.get("by_district") or [] if d and (_num(d.get("median_ppsm")) or 0) > 0]
    med = _num(s.get("median_ppsm")) or 0
    if len(ds) < 3:
        return page
    mx = max(float(d["median_ppsm"]) for d in ds)

    def h(v: float) -> str:
        return f"{Decimal(str(max(0.0, min(100.0, v / mx * 100)))).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"

    def side(d: Mapping) -> str:
        return "hi" if float(d["median_ppsm"]) >= med else "lo"

    cols = (
        f'<div class="dmed" style="bottom:{h(med)}"><span class="dmedl">медиана города</span></div>' if med > 0 else ""
    ) + "".join(
        f'<div class="dcol {side(d)}"><span class="dbar" style="height:{h(float(d["median_ppsm"]))}"></span></div>'
        for d in ds
    )
    labs = []
    for d in ds:
        v = float(d["median_ppsm"])
        dl = _js_round((v - med) / med * 100) if med > 0 else 0
        sign = "+" if dl > 0 else "−" if dl < 0 else ""
        labs.append(
            f'<div class="dl {side(d)}"><span class="dpx">{_js_round(v / 1000)}<i>тыс ₸/м²</i></span>'
            f'<span class="dnm">{html.escape(str(d.get("district") or ""))}</span>'
            + (f'<span class="ddl"><b>{sign}{abs(dl)}%</b> к медиане</span>' if med > 0 else "")
            + f'<span class="dhbar"><i style="width:{h(v)}"></i></span></div>'
        )
    m = _BARS_RE.search(page)
    if not m:
        return page
    return (
        page[: m.start()]
        + m.group("cols_open") + cols + m.group("mid") + "".join(labs) + m.group("end")
        + page[m.end():]
    )


# ------------------------------------------------------------------- JSON-LD


def _json_ld(data: Mapping) -> str:
    # «</» внутри строки закрыл бы <script> раньше времени
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/ld+json">{body}</script>'


_TAG_RE = re.compile(r"<[^>]+>")
# границы блоков — пробел, иначе абзацы ответа слипаются: «…не восстановить.Текст…»
_BLOCK_TAG_RE = re.compile(r"</?(?:p|br|li|ul|ol|div|tr|td|th|h[1-6])\b[^>]*>", flags=re.I)
_QA_RE = re.compile(
    r'<div class="qa"><button class="q"[^>]*>(?P<q>.*?)<span class="qi"></span></button>'
    r'<div class="a"[^>]*>(?P<a>.*?)</div></div>',
    flags=re.S,
)


def _text(fragment: str) -> str:
    """Видимый текст фрагмента разметки: без тегов, сущности раскрыты, пробелы схлопнуты."""
    text = _TAG_RE.sub("", _BLOCK_TAG_RE.sub(" ", fragment))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def faq_items(page: str) -> list[tuple[str, str]]:
    """(вопрос, ответ) из блоков «Частые вопросы» (.qa) — в том виде, как их видит человек."""
    items = []
    for m in _QA_RE.finditer(page):
        q, a = _text(m.group("q")), _text(m.group("a"))
        if q and a:
            items.append((q, a))
    return items


def faq_json_ld(page: str) -> str | None:
    """FAQPage из видимых вопросов страницы; None — вопросов на странице нет."""
    items = faq_items(page)
    if not items:
        return None
    return _json_ld({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in items
        ],
    })


def inject_head(page: str, snippet: str) -> str:
    """Дописывает snippet в конец <head> (перед первым </head>)."""
    if not snippet or "</head>" not in page:
        return page
    return page.replace("</head>", snippet + "\n</head>", 1)


# ------------------------------------------------------------ «Рынок»: аренда

RENT_TITLE = "Аренда квартир в Алматы — цены по районам и комнатам │ baǵam"
RENT_DESCRIPTION = (
    "Сколько стоит аренда квартиры в Алматы: типичная аренда по районам и комнатам, "
    "разброс цен, динамика по неделям и доходность сдачи. Данные krisha.kz."
)
RENT_OG_DESCRIPTION = (
    "Сколько стоит аренда квартиры в Алматы: типичная аренда по районам и комнатам, "
    "разброс цен, динамика по неделям и доходность сдачи."
)


def _set_attr_content(page: str, attr: str, name: str, value: str) -> str:
    rx = re.compile(rf'(<meta {attr}="{re.escape(name)}" content=")[^"]*(")')
    return rx.sub(lambda m: m.group(1) + html.escape(value) + m.group(2), page, count=1)


def rent_variant(page: str) -> str:
    """stats.html для /stats?mode=rent: свои title, description и canonical.

    Режим страницы выбирает скрипт по ?mode=rent, а разметка у обоих режимов
    одна. Без этого варианта аренда делила с продажей title и canonical, и
    поисковик не мог показать её отдельно по запросам про аренду.
    """
    page = re.sub(r"<title>[^<]*</title>", f"<title>{html.escape(RENT_TITLE)}</title>", page, count=1)
    page = _set_attr_content(page, "name", "description", RENT_DESCRIPTION)
    page = _set_attr_content(page, "property", "og:title", RENT_TITLE)
    page = _set_attr_content(page, "property", "og:description", RENT_OG_DESCRIPTION)
    # без JS режим выбирает CSS по html[data-mode]: иначе краулер видит тексты продажи
    page = re.sub(r"<html\b(?![^>]*\sdata-mode=)", '<html data-mode="rent"', page, count=1)
    page = re.sub(r'(<link rel="canonical" href="[^"?]*/stats)(")', r"\1?mode=rent\2", page, count=1)
    return re.sub(r'(<meta property="og:url" content="[^"?]*/stats)(")', r"\1?mode=rent\2", page, count=1)


# --------------------------------------------------------- сводка без JS

NOSCRIPT_MARKER = '<noscript><p class="rnote">Цифры рынка подгружаются скриптом — включите JavaScript.</p></noscript>'


def _mln(x: float) -> str:
    return fmt(x / 1e6, 1).removesuffix(",0") + NB + "млн" + NB + "₸"


def _tys(x: float) -> str:
    return ru(x / 1000) + NB + "тыс" + NB + "₸"


def _sale_block(s: Mapping) -> str:
    total, ppsm, price = (_num(s.get(k)) for k in ("total_listings", "median_ppsm", "median_price"))
    if total is None or ppsm is None:
        return ""
    date = almaty_date(s.get("updated_at"))
    lead = (
        f"<p><b>Продажа квартир в Алматы{' на ' + date if date else ''}.</b> "
        f"{ru(total)} {plural(int(total), ('активное объявление', 'активных объявления', 'активных объявлений'))} на krisha.kz; "
        f"типичная цена квадратного метра — {_tys(ppsm)}"
        + (f", типичная цена квартиры — {_mln(price)}" if price is not None else "")
        + ".</p>"
    )
    rows = []
    for d in s.get("by_district") or []:
        dp, dn, dprice = _num(d.get("median_ppsm")), _num(d.get("n")), _num(d.get("median_price"))
        if not d.get("district") or dp is None:
            continue
        rows.append(
            f"<tr><th scope=\"row\">{html.escape(str(d['district']))}</th><td>{_tys(dp)}</td>"
            f"<td>{_mln(dprice) if dprice is not None else '—'}</td><td>{ru(dn) if dn is not None else '—'}</td></tr>"
        )
    table = (
        "<table><caption>Цена метра по районам Алматы (медиана объявлений о продаже)</caption>"
        "<thead><tr><th scope=\"col\">Район</th><th scope=\"col\">Цена м²</th>"
        "<th scope=\"col\">Типичная цена квартиры</th><th scope=\"col\">Объявлений</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        if rows else ""
    )
    return lead + table


def _rent_block(r: Mapping) -> str:
    total, rent = _num(r.get("total_listings")), _num(r.get("median_rent"))
    if total is None or rent is None:
        return ""
    date = almaty_date(r.get("updated_at"))
    by_rooms = [x for x in r.get("by_rooms") or [] if _num(x.get("median_rent")) is not None and x.get("rooms")]
    rooms = "; ".join(f"{int(x['rooms'])}-комнатная — {_tys(x['median_rent'])}" for x in by_rooms[:3])
    lead = (
        f"<p><b>Аренда квартир в Алматы{' на ' + date if date else ''}.</b> "
        f"{ru(total)} {plural(int(total), ('объявление', 'объявления', 'объявлений'))} об аренде на krisha.kz; "
        f"типичная аренда — {_tys(rent)} в месяц"
        + (f" ({rooms})" if rooms else "")
        + ".</p>"
    )
    rows = []
    for d in r.get("by_district") or []:
        dr, dn, dy = _num(d.get("median_rent")), _num(d.get("n")), _num(d.get("gross_yield_pct"))
        if not d.get("district") or dr is None:
            continue
        rows.append(
            f"<tr><th scope=\"row\">{html.escape(str(d['district']))}</th><td>{_tys(dr)}</td>"
            f"<td>{pct(dy) if dy is not None else '—'}</td><td>{ru(dn) if dn is not None else '—'}</td></tr>"
        )
    table = (
        "<table><caption>Аренда по районам Алматы (медиана объявлений, в месяц)</caption>"
        "<thead><tr><th scope=\"col\">Район</th><th scope=\"col\">Аренда в месяц</th>"
        "<th scope=\"col\">Доходность сдачи в год</th><th scope=\"col\">Объявлений</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        if rows else ""
    )
    return lead + table


def market_noscript(sale: Mapping | None, rent: Mapping | None, *, rent_first: bool = False) -> str | None:
    """Сводка рынка для <noscript> «Рынка»: те же числа, что нарисует JS. None — данных нет."""
    blocks = [_sale_block(sale or {}), _rent_block(rent or {})]
    if rent_first:
        blocks.reverse()
    body = "".join(b for b in blocks if b)
    if not body:
        return None
    return (
        '<noscript><div class="nsdata">'
        "<p>Графики и переключатель на этой странице работают на JavaScript. Главные цифры — ниже.</p>"
        f"{body}<p>Цены — по объявлениям, а не по сделкам.</p></div></noscript>"
    )


def with_market_noscript(page: str, block: str | None) -> str:
    if not block or NOSCRIPT_MARKER not in page:
        return page
    return page.replace(NOSCRIPT_MARKER, block, 1)


# ------------------------------------------------------------------ llms.txt


def llms_txt(base: str, values: Mapping[str, str], sale: Mapping | None, rent: Mapping | None) -> str:
    """/llms.txt: что это за сервис, главные цифры и куда смотреть — простым текстом."""
    s, r = sale or {}, rent or {}
    facts = []
    date = almaty_date(s.get("updated_at"))
    total = _num(s.get("total_listings"))
    if values.get("total") and values.get("ppsmk") and total is not None:
        words = plural(int(total), ("активное объявление", "активных объявления", "активных объявлений"))
        facts.append(
            f"- Продажа{' на ' + date if date else ''}: {values['total']} {words} о продаже квартир"
            f" в Алматы на krisha.kz, медиана цены метра — {values['ppsmk']}"
            + (f", медиана цены квартиры — {values['medpricem']}" if values.get("medpricem") else "")
            + "."
        )
    rent_total, rent_med = _num(r.get("total_listings")), _num(r.get("median_rent"))
    if rent_total is not None and rent_med is not None:
        facts.append(
            f"- Аренда: {ru(rent_total)} {plural(int(rent_total), TOTAL_FORMS)} об аренде,"
            f" медиана аренды — {_tys(rent_med)} в месяц."
        )
    if values.get("mape"):
        facts.append(
            f"- Средняя ошибка оценки продажи — {values['mape']}"
            + (f", аренды — {values['rmape']}" if values.get("rmape") else "")
            + " (на объявлениях, которых модель не видела при обучении)."
        )
    facts_text = "\n".join(facts).replace(NB, " ")
    return (
        "# baǵam (bagam.info)\n\n"
        "> Бесплатный сервис: справедливая цена квартиры в Алматы по ссылке на объявление krisha.kz. "
        "Модель машинного обучения сравнивает квартиру с рынком — продажа и аренда — и показывает "
        "оценку, обычный диапазон цены для таких квартир и что на неё влияет.\n\n"
        "Главное:\n"
        "- Город — Алматы (Казахстан). Данные — открытые объявления krisha.kz, база обновляется ежедневно.\n"
        "- Оценка справочная: это ориентир для торга, а не оферта и не отчёт оценщика.\n"
        "- Цены рынка считаются по объявлениям, а не по сделкам.\n"
        + (facts_text + "\n" if facts_text else "")
        + "\n## Страницы\n\n"
        f"- [Оценка квартиры по ссылке]({base}/): вставьте ссылку на объявление krisha.kz — вердикт, справедливая цена, диапазон и факторы\n"
        f"- [Рынок продажи]({base}/stats): цены квартир и метра по районам и комнатам, динамика по неделям\n"
        f"- [Рынок аренды]({base}/stats?mode=rent): аренда по районам и комнатам, доходность сдачи\n"
        f"- [О проекте]({base}/about): откуда данные, как считает модель, точность и ограничения\n"
        f"- [Telegram-бот]({base}/bot): оценка в чате, слежение за ценой объявления, выгодные объявления\n"
        f"- [Условия использования]({base}/terms)\n"
        f"- [Политика конфиденциальности]({base}/privacy)\n\n"
        "## Данные\n\n"
        f"- [Сводка рынка продажи, JSON]({base}/api/stats)\n"
        f"- [Сводка рынка аренды, JSON]({base}/api/stats/rent)\n"
        f"- [Точность модели и свежесть данных, JSON]({base}/api/health)\n\n"
        "## Дополнительно\n\n"
        "- [Исходный код](https://github.com/Dex719/krisha-fair-price)\n"
        "- [Telegram-бот @fairprice_kzbot](https://t.me/fairprice_kzbot)\n"
    )
