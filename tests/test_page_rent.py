"""Аренда — режим страницы «Рынок» (/stats?mode=rent); /rent — постоянный редирект туда.

Общий контракт страницы — в test_market_redesign.py; здесь то, что касается именно аренды.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from krisha.api.app import app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static() -> str:
    return (STATIC / "stats.html").read_text(encoding="utf-8")


def _get(path: str, **kwargs):
    with TestClient(app) as client:
        return client.get(path, **kwargs)


def test_rent_route_redirects_to_market_rent_mode():
    """«Аренда» слилась с «Рынком»: /rent — постоянный редирект на /stats?mode=rent."""
    with TestClient(app) as client:
        resp = client.get("/rent", follow_redirects=False)
        head = client.head("/rent", follow_redirects=False)

    assert resp.status_code == 301
    assert resp.headers["location"] == "/stats?mode=rent"
    assert head.status_code == 301


def test_rent_redirect_keeps_other_query_params():
    with TestClient(app) as client:
        resp = client.get(
            "/rent?url=https://krisha.kz/a/show/1&mode=sale&utm=x", follow_redirects=False
        )

    assert resp.status_code == 301
    # свой mode у запроса аренду не перебивает, остальное переносится как было
    assert resp.headers["location"] == (
        "/stats?mode=rent&url=https%3A%2F%2Fkrisha.kz%2Fa%2Fshow%2F1&utm=x"
    )


def test_rent_page_file_is_gone():
    assert not (STATIC / "rent.html").exists()


def test_sitemap_has_no_rent_page():
    """/rent — редирект, в sitemap ему не место: аренда живёт на /stats."""
    resp = _get("/sitemap.xml")

    assert resp.status_code == 200
    assert "/rent</loc>" not in resp.text
    assert "/stats</loc>" in resp.text
    assert "/stats?mode=rent</loc>" in resp.text


def test_market_page_serves_both_modes():
    """Режим выбирает скрипт страницы, а у аренды свои title, description и
    canonical: иначе поисковик не покажет её отдельно (live_pages.rent_variant)."""
    with TestClient(app) as client:
        sale = client.get("/stats")
        rent = client.get("/stats?mode=rent")

    assert sale.status_code == rent.status_code == 200
    assert 'data-set="rent"' in rent.text and "/api/stats/rent" in rent.text
    assert '<link rel="canonical" href="https://bagam.info/stats">' in sale.text
    assert '<link rel="canonical" href="https://bagam.info/stats?mode=rent">' in rent.text
    assert '<meta property="og:url" content="https://bagam.info/stats?mode=rent">' in rent.text
    assert "<title>Аренда квартир в Алматы — цены по районам и комнатам │ baǵam</title>" in rent.text
    # заголовок — тот же, что ставит скрипт страницы при переключении режима
    assert "Аренда квартир в Алматы — цены по районам и комнатам │ baǵam" in _static()
    # остальная страница — та же (у аренды ещё html[data-mode=rent] — для краулеров без JS)
    assert '<html data-mode="rent" lang="ru">' in rent.text

    def body(html: str) -> str:
        return re.sub(r"<html[^>]*>|<head>.*?</head>|<noscript>.*?</noscript>", "", html, flags=re.S)

    assert body(sale.text) == body(rent.text)


def test_rent_mode_nav_marks_market_and_footer_link():
    """В шапке подсвечен «Рынок» (один пункт на оба режима), в подвале есть «Рынок аренды»."""
    html = _static()
    menu = re.search(r'<div class="menu">(.*?)</div>', html).group(1)

    assert '<a href="/stats" aria-current="page">Рынок</a>' in menu
    assert menu.count('aria-current="page"') == 1 and "Аренда" not in menu
    assert '<a class="flink" href="/stats?mode=rent">Рынок аренды</a>' in html
    # aria-current в подвале переставляет общий site.js после смены режима
    assert "B.markCurrent()" in html


def test_rent_mode_texts():
    html = _static()

    assert '<span data-show="rent">Аренда по неделям</span>' in html
    assert '<span data-show="rent">Сколько просят за аренду</span>' in html
    assert "Данные об аренде временно недоступны" in html
    assert "Аренда квартир в Алматы — цены по районам и комнатам │ baǵam" in html
    # аренда в главных цифрах — за квартиру в месяц, не за метр
    assert "₸/м² в мес" not in html and "Аренда за м² в месяц" not in html


def test_rent_charts_need_data_and_stay_keyboard_friendly():
    html = _static()

    assert "tr.length < 3" in html
    assert 'id="trend"' in html and 'data-r="trendsec"' in html
    # гистограмма — список с подписями, без кнопок; график — одна остановка Tab
    assert '<ol class="hist"' in html
    assert html.count('tabindex="0"') == 1


def test_rent_room_rows_use_data_thresholds():
    """«Мало объявлений» решают данные: меньше 100 — «~», меньше 30 — строки нет, есть пояснение."""
    html = _static()

    assert "MIN_ROOMS = 30, THIN = 100" in html
    assert "'«~» — меньше ' + THIN + ' объявлений, цифра ориентировочная.'" in html
