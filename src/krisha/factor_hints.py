"""Подсказки к факторам цены: реальная статистика из нашей базы.

Для каждого фактора из top_factors строим короткое объяснение со значением
квартиры и сравнением с рынком (медианы ₸/м² из data/krisha.db).
Статистика считается один раз на процесс и кэшируется.
"""

from __future__ import annotations

import logging
import statistics
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


@lru_cache(maxsize=1)
def market_stats() -> dict[str, Any]:
    """Медианы ₸/м² по срезам базы. Пустой dict, если базы нет."""
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
            for name, lo, hi in [("new", 0, 5), ("mid_age", 5, 25), ("old", 25, 200)]:
                stats[f"age_{name}"], _ = med(
                    " AND year_built IS NOT NULL AND 2026 - year_built >= ? AND 2026 - year_built < ?",
                    (lo, hi),
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
    where = f"Этаж {floor} из {total}"
    if floor == total and total >= 2:
        pct = _pct(s["last_floor"], s["mid_floor"]) if s.get("last_floor") and s.get("mid_floor") else None
        stat = f" По нашей базе последние этажи в среднем на {abs(pct)}% дешевле за м², чем средние." if pct else ""
        return f"{where} — последний: покупатели опасаются протечек крыши и жары летом, такие квартиры обычно уходят дольше.{stat}"
    if floor == 1:
        pct = _pct(s["first_floor"], s["mid_floor"]) if s.get("first_floor") and s.get("mid_floor") else None
        stat = f" По нашей базе первые этажи в среднем на {abs(pct)}% дешевле за м²." if pct else ""
        return f"{where} — первый: шум улицы, меньше приватности и света.{stat}"
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


def _age_hint(listing: dict, s: dict) -> str | None:
    year = listing.get("year_built")
    if not year:
        return None
    age = 2026 - int(year)
    bucket = "new" if age < 5 else ("mid_age" if age < 25 else "old")
    label = {"new": "новостройки (до 5 лет)", "mid_age": "дома 5–25 лет", "old": "дома старше 25 лет"}[bucket]
    med = s.get(f"age_{bucket}")
    stat = f" Медиана для сегмента «{label}»: {_fmt_k(med)} против {_fmt_k(s['city'])} по городу." if med else ""
    return f"Дом {year} года (возраст {age} лет): свежий фонд ценится выше — современные планировки, коммуникации, паркинги.{stat}"


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
        "rooms": f"{rooms}-комнатная: число комнат задаёт сегмент спроса — однушки самые ликвидные, многокомнатные продаются дольше." if rooms else None,
        "ceiling": f"Потолки {ceiling:g} м: от 2.8 м считается премиальным признаком, ниже 2.5 м — заметный минус." if ceiling else None,
        "photos_count": f"{photos} фото в объявлении: косвенный сигнал — у качественных объявлений от собственников обычно больше фотографий." if photos is not None else None,
        "dist_center_km": f"До центра {dist_c:.1f} км: близость к центру — устойчивая надбавка к цене за м²." if dist_c is not None else None,
        "user_type": "Кто продаёт: у застройщиков и компаний цены обычно выше заявлены, у собственников больше пространство для торга.",
        "building_type": "Материал дома: монолит и кирпич ценятся выше панели — лучше шумоизоляция и долговечность.",
        "complex_name": "Жилой комплекс: имя ЖК тянет за собой класс жилья, застройщика и инфраструктуру двора.",
        "housing_class": "Класс жилья (комфорт/бизнес/элит) — один из самых сильных факторов цены за м² в новостройках.",
        "developer": "Репутация застройщика влияет на доверие покупателей и цену.",
        "walk_score": "Пешая доступность: сколько повседневных точек (школы, магазины, остановки) в радиусе пешком — выше балл, дороже м².",
        "district_ppsm": "Средний уровень цен в районе — модель опирается на него как на базовую «температуру» локации.",
        "micro_median_ppsm": "Средний уровень цен микрорайона — более точная «температура» локации, чем район.",
        "microdistrict_ppsm": "Средний уровень цен микрорайона — более точная «температура» локации, чем район.",
        "district_median_ppsm": "Средний уровень цен в районе — базовая «температура» локации для модели.",
        "is_new_building": "Новостройка или вторичка: новостройки в среднем дороже за м², но без отделки.",
        "renovation": "Состояние ремонта напрямую конвертируется в цену: «евроремонт» против «черновой отделки» — разница в миллионы.",
        "furniture": "Мебель в придачу — небольшой, но реальный плюс к цене.",
        "parking": "Паркинг — дефицит в Алматы, заметная надбавка.",
        "balcony": "Балкон/лоджия добавляют полезной площади и света.",
        "toilet": "Раздельный санузел традиционно ценится выше совмещённого.",
        "security_count": "Охрана, домофон, видеонаблюдение — каждый пункт безопасности добавляет привлекательности.",
        "dist_metro_km": "Близость метро — редкий и сильный плюс для Алматы.",
        "dist_school_km": "Школа рядом — важно семьям, расширяет круг покупателей.",
        "dist_kindergarten_km": "Детсад в пешей доступности — плюс для семей с детьми.",
        "dist_park_km": "Парк рядом — экология и прогулки, устойчивый плюс.",
        "dist_supermarket_km": "Супермаркет рядом — бытовое удобство, небольшой плюс.",
        "dist_bus_stop_km": "Остановка рядом — важно для районов без метро.",
        "dist_big_road_km": "Магистраль под окнами — шум и пыль, минус; но подъезд удобнее.",
        "dist_industrial_km": "Промзона рядом — экология и вид, заметный минус.",
        "total_floors": "Этажность дома: высотки чаще новостройки с лифтами и паркингом, малоэтажки — старый фонд.",
        "year_built": None,  # обрабатывается _age_hint
    }


def build_factor_hints(listing: dict, factors: list[dict]) -> list[dict]:
    """Добавляет каждому фактору поле hint (или None)."""
    s = market_stats()
    if not s:
        return factors
    floor_keys = {"floor", "floor_ratio", "is_first_floor", "is_last_floor"}
    district_keys = {"district", "microdistrict"}
    generic = _generic_hints(listing, s)
    for f in factors:
        feat = f["feature"]
        hint = None
        if feat in floor_keys:
            hint = _floor_hint(listing, s)
        elif feat in district_keys:
            hint = _district_hint(listing, s)
        elif feat == "area":
            hint = _area_hint(listing, s)
        elif feat in {"year_built", "building_age"}:
            hint = _age_hint(listing, s)
        else:
            hint = generic.get(feat)
        f["hint"] = hint
    return factors
