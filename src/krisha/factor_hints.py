"""Подсказки к факторам цены: реальная статистика из нашей базы.

Для каждого фактора из top_factors строим короткое объяснение со значением
квартиры и сравнением с рынком (медианы ₸/м² из data/krisha.db).
Статистика считается один раз на процесс и кэшируется.
"""

from __future__ import annotations

import logging
import statistics
import time
from functools import lru_cache
from typing import Any

from krisha.config import DB_PATH
from krisha.db import get_conn

logger = logging.getLogger(__name__)

# Только активные объявления: медианы должны отражать текущий рынок
_PPSM_SQL = (
    "SELECT price * 1.0 / area FROM listings "
    "WHERE is_active = 1 AND price > 0 AND area > 0"
)


def _fmt_k(value: float) -> str:
    """1001825.2 -> '1 002 тыс ₸/м²'."""
    return f"{round(value / 1000):,}".replace(",", " ") + " тыс ₸/м²"


def _pct(part: float, base: float) -> int:
    return round((part - base) / base * 100)


# Медианы меняются только с новой базой, а новая база приходит рестартом
# Space (скачивается при старте). Ключом раньше был mtime файла — но его
# двигает сам предикт: upsert лота + лог предикта, и при закрытии последнего
# соединения SQLite сливает WAL в файл. Кэш сбрасывался каждым запросом,
# и каждая проверка ссылки платила ~1.9 с за пересчёт (замер на снимке прода).
MARKET_STATS_TTL_S = 6 * 3600


def market_stats() -> dict[str, Any]:
    return _market_stats_cached(str(DB_PATH), int(time.time() // MARKET_STATS_TTL_S))


@lru_cache(maxsize=2)
def _market_stats_cached(db_path: str, _bucket: int) -> dict[str, Any]:
    """Медианы ₸/м² по срезам базы. Пустой dict, если базы нет."""
    # Проверяем ДО get_conn: sqlite3.connect на отсутствующий путь молча
    # создаёт пустой файл, и дальше всё, что судит по DB_PATH.exists()
    # («нет базы → 503» в /api/demo), видит базу без таблиц. Так в CI первый
    # же предикт карточки ронял соседний тест (PR #194).
    if not DB_PATH.exists():
        return {}
    try:
        with get_conn(DB_PATH) as conn:
            def med(where: str = "", args: tuple = ()) -> tuple[float | None, int]:
                rows = [r[0] for r in conn.execute(_PPSM_SQL + where, args)]
                return (statistics.median(rows) if rows else None), len(rows)

            city, n_city = med()
            if not city:
                return {}
            stats: dict[str, Any] = {"city": city, "n_city": n_city}
            stats["last_floor"], _ = med(" AND floor = total_floors AND total_floors >= 5")
            stats["first_floor"], _ = med(" AND floor = 1 AND total_floors >= 5")
            stats["mid_floor"], _ = med(" AND floor > 1 AND floor < total_floors AND total_floors >= 5")
            stats["district"] = {
                r[0]: (r[1], r[2])
                for r in conn.execute(
                    """SELECT district, price * 1.0 / area, COUNT(*) FROM listings
                       WHERE is_active = 1 AND price > 0 AND area > 0
                         AND district IS NOT NULL
                       GROUP BY district"""
                )
            }
            # медианы через group by в sqlite нет — пересчитаем честно
            for d in list(stats["district"]):
                m, n = med(" AND district = ?", (d,))
                stats["district"][d] = (m, n)
            from krisha.features import current_year

            year_now = current_year()
            for name, lo, hi in [("new", 0, 5), ("mid_age", 5, 25), ("old", 25, 200)]:
                stats[f"age_{name}"], _ = med(
                    " AND year_built IS NOT NULL AND ? - year_built >= ? AND ? - year_built < ?",
                    (year_now, lo, year_now, hi),
                )
            for name, lo, hi in [("small", 0, 40), ("mid", 40, 70), ("big", 70, 1000)]:
                stats[f"area_{name}"], _ = med(" AND area >= ? AND area < ?", (lo, hi))
            return stats
    except Exception:  # noqa: BLE001 — подсказки не должны ронять предикт
        logger.exception("market_stats failed")
        return {}


def _floor_hint(listing: dict, s: dict) -> str | None:
    floor, total = listing.get("floor"), listing.get("total_floors")
    if not floor or not total:
        return None
    floor, total = int(floor), int(total)
    where = f"Этаж {floor} из {total}"
    if floor == total and total >= 2:
        pct = _pct(s["last_floor"], s["mid_floor"]) if s.get("last_floor") and s.get("mid_floor") else None
        # Слово подбираем по знаку: pct считается как отклонение последних
        # этажей от средних и вполне бывает положительным (верхние этажи с
        # видом дороже). Захардкоженное «дешевле» рядом с abs() выдавало
        # прямо противоположное правде утверждение.
        stat = (
            f" По нашей базе последние этажи в среднем на {abs(pct)}% "
            f"{'дешевле' if pct < 0 else 'дороже'} за м², чем средние."
        ) if pct else ""
        extra = ""
        if total >= 6:
            extra = f" Плюс зависимость от лифта: сломается — подъём на {floor}-й пешком."
        if total >= 9:
            extra += (
                " Алматы — сейсмоопасная зона, на верхних этажах толчки"
                " ощущаются сильнее, часть покупателей сознательно ищет ниже."
            )
        return (
            f"{where} — последний: покупатели опасаются протечек крыши и жары летом."
            f"{extra} Такие квартиры обычно уходят дольше.{stat}"
        )
    if floor == 1:
        pct = _pct(s["first_floor"], s["mid_floor"]) if s.get("first_floor") and s.get("mid_floor") else None
        stat = (
            f" По нашей базе первые этажи в среднем на {abs(pct)}% "
            f"{'дешевле' if pct < 0 else 'дороже'} за м²."
        ) if pct else ""
        return f"{where} — первый: шум улицы, меньше приватности и света.{stat}"
    if floor >= 9:
        return (
            f"{where}: сверху тише и лучше вид, но выше зависимость от лифта,"
            " а в сейсмоопасном Алматы верхние этажи ощутимо качает при толчках —"
            " это сужает круг покупателей."
        )
    return f"{where} — средние этажи самые ликвидные: нет минусов первого и последнего, дисконта не требуется."


def _district_hint(listing: dict, s: dict) -> str | None:
    from krisha.stats import DISTRICT_RU

    d = listing.get("district")
    info = (s.get("district") or {}).get(d)
    if not d or not info or not info[0]:
        return None
    med, n = info
    pct = _pct(med, s["city"])
    direction = "выше" if pct > 0 else "ниже"
    return (
        f"Медиана по району {DISTRICT_RU.get(d, d)}: {_fmt_k(med)} — "
        f"на {abs(pct)}% {direction} средней по Алматы ({_fmt_k(s['city'])}, {n} квартир в выборке)."
    )


def _area_hint(listing: dict, s: dict) -> str | None:
    area = listing.get("area")
    if not area:
        return None
    bucket = "small" if area < 40 else ("mid" if area < 70 else "big")
    label = {"small": "до 40 м²", "mid": "40–70 м²", "big": "от 70 м²"}[bucket]
    med = s.get(f"area_{bucket}")
    stat = f" Медиана в сегменте {label}: {_fmt_k(med)} (компактные квартиры дороже за м², большие — дешевле, но дороже целиком)." if med else ""
    return f"Площадь {area:g} м² — один из главных факторов: цена растёт с метражом почти линейно.{stat}"


def _years(n: int) -> str:
    """1 год, 2 года, 5 лет, 11 лет, 21 год."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} год"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} года"
    return f"{n} лет"


def _known(value: Any) -> Any:
    """Значение факта или None: NaN, пустая строка и MISSING_CAT — «не указано»."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in ("unknown", "nan", "none"):
            return None
        return text
    return value


def _fact(listing: dict, facts: dict, key: str) -> Any:
    """Сначала сырое поле объявления, потом строка фич модели (там — класс
    жилья и застройщик из справочника ЖК, признак новостройки)."""
    value = _known(listing.get(key))
    return value if value is not None else _known(facts.get(key))


def _house_age(listing: dict, facts: dict) -> int | None:
    from krisha.features import current_year

    year = _fact(listing, facts, "year_built")
    try:
        return current_year() - int(year) if year is not None else None
    except (TypeError, ValueError):
        return None


def _is_new(listing: dict, facts: dict) -> bool | None:
    """Новостройка ли: признак модели, иначе категория krisha. None — неизвестно."""
    flag = _known(facts.get("is_new_building"))
    if flag is not None:
        try:
            return bool(int(flag))
        except (TypeError, ValueError):
            pass
    category = _fact(listing, facts, "category")
    if category is not None:
        return category == "novostroiki"
    return None


def _age_hint(listing: dict, s: dict) -> str | None:
    from krisha.features import current_year

    year = listing.get("year_built")
    if not year:
        return None
    age = current_year() - int(year)
    bucket = "new" if age < 5 else ("mid_age" if age < 25 else "old")
    label = {"new": "новостройки (до 5 лет)", "mid_age": "дома 5–25 лет", "old": "дома старше 25 лет"}[bucket]
    med = s.get(f"age_{bucket}")
    stat = ""
    if med:
        stat = f" Медиана для сегмента «{label}»: {_fmt_k(med)} против {_fmt_k(s['city'])} по городу"
        # Медиана сегмента смешивает возраст с районом: старые дома стоят
        # ближе к центру, новостройки — на окраинах. Без пояснения цифра
        # спорит с текстом подсказки.
        if bucket == "old" and med > s["city"]:
            stat += " — старые дома чаще стоят ближе к центру"
        elif bucket == "new" and med < s["city"]:
            stat += " — новостройки чаще строят на окраинах"
        stat += "."
    if age <= 1:
        return f"Дом {year} года — новый: свежий фонд ценится выше — современные планировки, коммуникации, паркинги.{stat}"
    if bucket == "old":
        return (
            f"Дом {year} года (ему {_years(age)}): при прочих равных старый фонд дешевле — "
            f"износ коммуникаций, старые планировки, редко есть паркинг.{stat}"
        )
    return f"Дом {year} года (ему {_years(age)}): свежий фонд ценится выше — современные планировки, коммуникации, паркинги.{stat}"


def _housing_class_hint(listing: dict, facts: dict) -> str:
    cls = _fact(listing, facts, "housing_class")
    if cls is None:
        # Раньше здесь было «один из самых сильных факторов цены в новостройках»
        # — и под домом 1970 года это читалось как ошибка.
        return (
            "Класс жилья (комфорт, бизнес…) у этого дома не указан — так обычно у старых "
            "домов и домов вне ЖК. В новых ЖК класс заметно влияет на цену за м²."
        )
    return f"Класс жилья «{cls}»: в ЖК класс (эконом, комфорт, бизнес, премиум) — один из сильных факторов цены за м²."


def _developer_hint(listing: dict, facts: dict) -> str:
    dev = _fact(listing, facts, "developer")
    if dev is None:
        return "Застройщик не указан — обычное дело для вторички. У новостроек репутация застройщика влияет на цену."
    return f"Застройщик — {dev}: его репутация влияет на доверие покупателей и цену."


def _total_floors_hint(listing: dict, facts: dict) -> str:
    total = _fact(listing, facts, "total_floors")
    try:
        total = int(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    if not total:
        return "Этажность дома: модель связывает её с возрастом и типом дома."
    age, new = _house_age(listing, facts), _is_new(listing, facts)
    old = age is not None and age >= 25 and not new
    fresh = bool(new) or (age is not None and age < 15)
    where = f"{total}-этажный дом"
    if total >= 10:
        if old:
            year = int(_fact(listing, facts, "year_built"))
            return f"{where} {year} года: у старых многоэтажек цену тянут вниз износ лифтов и коммуникаций."
        return f"{where}: высотки — чаще новые ЖК с лифтами и паркингом."
    if total <= 5:
        if fresh:
            return f"{where}: малоэтажные новые дома — тихие, с небольшим числом соседей."
        if old:
            return f"{where}: малоэтажки этих лет — старый фонд, обычно без лифта и паркинга."
        return f"{where}: малоэтажки в Алматы — чаще старый фонд, но бывают и новые клубные дома."
    return f"{where}: средняя этажность бывает и у старого фонда, и у новых ЖК — модель смотрит на неё вместе с возрастом дома."


def _new_building_hint(listing: dict, facts: dict) -> str:
    new = _is_new(listing, facts)
    if new is None:
        return "Новостройка или вторичка: новостройки в среднем дороже за м², но часто без отделки."
    if new:
        return "Новостройка: в среднем дороже вторички за м², но часто без отделки."
    return "Вторичка: за м² обычно дешевле новостроек, зато дом уже обжит и видно соседей."


def _complex_hint(listing: dict, facts: dict) -> str:
    if _fact(listing, facts, "complex_name") is None:
        return "Дом не относится к жилому комплексу — обычное дело для старого фонда. У квартир в ЖК цену во многом задаёт сам комплекс."
    return "Жилой комплекс: имя ЖК тянет за собой класс жилья, застройщика и инфраструктуру двора."


def _building_type_hint(listing: dict, facts: dict) -> str:
    kind = str(_fact(listing, facts, "building_type") or "").lower()
    if "панел" in kind:
        return "Панельный дом: панель ценится ниже монолита и кирпича — хуже шумо- и теплоизоляция."
    if "монолит" in kind or "кирпич" in kind:
        return "Монолит и кирпич ценятся выше панели — лучше шумоизоляция и долговечность."
    return "Материал дома: монолит и кирпич ценятся выше панели — лучше шумоизоляция и долговечность."


def _ceiling_hint(ceiling: Any) -> str | None:
    if not ceiling:
        return None
    if ceiling >= 2.8:
        return f"Потолки {ceiling:g} м — премиальный признак (от 2.8 м)."
    if ceiling < 2.5:
        return f"Потолки {ceiling:g} м: ниже 2.5 м — заметный минус."
    return f"Потолки {ceiling:g} м — стандартная высота: премией считается от 2.8 м, минусом — ниже 2.5 м."


def _security_hint(listing: dict, facts: dict) -> str:
    count = _known(facts.get("security_count"))
    try:
        count = int(count) if count is not None else None
    except (TypeError, ValueError):
        count = None
    if count == 0:
        return "Охрана, домофон, видеонаблюдение в объявлении не указаны — покупатели ценят их наличие."
    if count:
        return f"Опций безопасности в объявлении: {count} (охрана, домофон, видеонаблюдение) — покупатели это ценят."
    return "Охрана, домофон, видеонаблюдение — каждый пункт безопасности добавляет привлекательности."


def _parking_hint(listing: dict, facts: dict) -> str:
    parking = _fact(listing, facts, "parking")
    if parking is None:
        return "Паркинг в объявлении не указан, а в Алматы он в дефиците — квартиры с паркингом дороже."
    return f"Паркинг: {str(parking).lower()} — в Алматы дефицит, заметная надбавка к цене."


def _conditional_hints(listing: dict, facts: dict) -> dict[str, str | None]:
    """Подсказки, текст которых зависит от фактов объявления: общий текст
    («высотки чаще новостройки») под конкретным лотом мог ему противоречить."""
    return {
        "housing_class": _housing_class_hint(listing, facts),
        "developer": _developer_hint(listing, facts),
        "total_floors": _total_floors_hint(listing, facts),
        "is_new_building": _new_building_hint(listing, facts),
        "category": _new_building_hint(listing, facts),
        "complex_name": _complex_hint(listing, facts),
        "building_type": _building_type_hint(listing, facts),
        "security_count": _security_hint(listing, facts),
        "parking": _parking_hint(listing, facts),
    }


def _generic_hints(listing: dict, s: dict) -> dict[str, str | None]:
    """Подсказки, не требующие отдельных функций."""
    rooms = listing.get("rooms")
    ceiling = listing.get("ceiling")
    photos = listing.get("photos_count")
    dist_c = listing.get("dist_center_km")
    lat, lon = listing.get("lat"), listing.get("lon")
    if dist_c is None and lat and lon:
        from krisha.config import ALMATY_CENTER
        from krisha.features import haversine_km

        dist_c = haversine_km(lat, lon, *ALMATY_CENTER)
    geo_hint = (
        f"Координаты дома: модель учит цену «по карте». До центра ~{dist_c:.1f} км." if dist_c is not None
        else "Координаты дома: модель учит цену «по карте» — соседние дома задают уровень."
    )
    return {
        "lat": geo_hint,
        "lon": geo_hint,
        "ceiling": _ceiling_hint(ceiling),
        "rooms": f"{rooms}-комнатная: число комнат задаёт сегмент спроса — однушки самые ликвидные, многокомнатные продаются дольше." if rooms else None,
        "photos_count": f"{photos} фото в объявлении: косвенный сигнал — у качественных объявлений от собственников обычно больше фотографий." if photos is not None else None,
        "dist_center_km": f"До центра {dist_c:.1f} км: близость к центру — устойчивая надбавка к цене за м²." if dist_c is not None else None,
        "user_type": "Кто продаёт: у застройщиков и компаний цены обычно выше заявлены, у собственников больше пространство для торга.",
        "walk_score": "Пешая доступность: сколько повседневных точек (школы, магазины, остановки) в радиусе пешком — выше балл, дороже м².",
        "district_ppsm": "Средний уровень цен в районе — модель опирается на него как на базовую «температуру» локации.",
        "micro_median_ppsm": "Средний уровень цен микрорайона — более точная «температура» локации, чем район.",
        "microdistrict_ppsm": "Средний уровень цен микрорайона — более точная «температура» локации, чем район.",
        "district_median_ppsm": "Средний уровень цен в районе — базовая «температура» локации для модели.",
        "hex7_ppsm": "Медианная цена м² в гексагоне ~2 км вокруг дома — «температура» округи точнее района.",
        "hex8_ppsm": "Медианная цена м² в квартале ~500 м вокруг дома — самая точная локальная «температура».",
        "knn_ppsm": "Медианная цена м² ближайших домов-соседей по карте.",
        "knn_n": "Сколько активных объявлений рядом — плотность локального предложения.",
        "renovation": "Состояние ремонта напрямую конвертируется в цену: «евроремонт» против «черновой отделки» — разница в миллионах.",
        "furniture": "Мебель в придачу — небольшой, но реальный плюс к цене.",
        "balcony": "Балкон/лоджия добавляют полезной площади и света.",
        "toilet": "Раздельный санузел традиционно ценится выше совмещённого.",
        "dist_metro_km": "Близость метро — редкий и сильный плюс для Алматы.",
        "dist_school_km": "Школа рядом — важно семьям, расширяет круг покупателей.",
        "dist_kindergarten_km": "Детсад в пешей доступности — плюс для семей с детьми.",
        "dist_park_km": "Парк рядом — экология и прогулки, устойчивый плюс.",
        "dist_supermarket_km": "Супермаркет рядом — бытовое удобство, небольшой плюс.",
        "dist_bus_stop_km": "Остановка рядом — важно для районов без метро.",
        "dist_big_road_km": "Магистраль под окнами — шум и пыль, минус; но подъезд удобнее.",
        "dist_industrial_km": "Промзона рядом — экология и вид, заметный минус.",
        "year_built": None,  # обрабатывается _age_hint
    }


def build_factor_hints(
    listing: dict, factors: list[dict], facts: dict[str, Any] | None = None
) -> list[dict]:
    """Добавляет каждому фактору поле hint (или None).

    Ключи факторов — после слияния коллинеарных признаков
    (predict.FACTOR_GROUPS): building_age, floor, district, lat…; исходные
    имена признаков тоже понимаем — на случай вызова без слияния.
    facts — строка фич модели: по ней подсказки подстраиваются под лот.
    """
    s = market_stats()
    if not s:
        return factors
    facts = facts or {}
    floor_keys = {"floor", "floor_ratio", "is_first_floor", "is_last_floor"}
    district_keys = {"district", "microdistrict"}
    generic = _generic_hints(listing, s)
    conditional = _conditional_hints(listing, facts)
    for f in factors:
        feat = f["feature"]
        hint = None
        if feat in floor_keys:
            hint = _floor_hint(listing, s)
        elif feat in district_keys:
            hint = _district_hint(listing, s)
            if hint is None and feat == "district":
                hint = generic.get("district_ppsm")
        elif feat == "area":
            hint = _area_hint(listing, s)
        elif feat in {"year_built", "building_age"}:
            hint = _age_hint(listing, s)
        elif feat in conditional:
            hint = conditional[feat]
        else:
            hint = generic.get(feat)
        f["hint"] = hint
    return factors
