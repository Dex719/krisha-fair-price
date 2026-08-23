"""Контракт главной страницы после редизайна baǵam.

Проверяем не красоту, а то, что легко потерять при правке вёрстки: мета-теги,
живые источники данных, отсутствие зашитых цифр, работающие пути ошибок.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from krisha import db
from krisha.api import app as app_module
from krisha.api.app import CSP, app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_home_uses_bagam_meta_design_css_and_local_favicon():
    html = _static("index.html")

    assert (
        "<title>Справедливая цена квартиры в Алматы по ссылке с Krisha │ baǵam</title>"
        in html
    )
    assert '<meta name="description"' in html
    assert 'href="/static/design.css"' in html
    assert 'rel="icon" type="image/svg+xml" href="/static/favicon.svg"' in html
    assert '<a href="/stats">Рынок</a>' in html
    assert '<a href="/about">О проекте</a>' in html
    # карточка для соцсетей и канонический адрес — их легко потерять при пересборке
    assert 'property="og:title"' in html and 'property="og:image"' in html
    assert 'rel="canonical"' in html
    # ничего чужого: шрифты и анимации со своего домена
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert "cdnjs.cloudflare.com" not in html


def test_design_css_holds_tokens_and_key_components():
    css = _static("design.css")

    for token in (
        "--bg:#0A100C",
        "--ink:#F1F2EB",
        "--dim:#8FA093",
        "--lime:#3ADC7C",
        "--vio:#E9B44C",
        "--panel:#101711",
        "--line:#1F2A21",
    ):
        assert token in css, f"пропал токен {token}"
    assert "html[data-theme=light]" in css, "светлая тема"
    for component in (
        ".sheet",          # карточка разбора
        ".verdict",        # плашка вердикта
        ".rwarn",          # предупреждения по объявлению
        ".rextra",         # история цены и похожие лоты
        ".fx",             # факторы модели
        ".rchip",          # чипы под отчётом
        ".inbar",          # строка ввода ссылки
    ):
        assert component in css, f"пропал компонент {component}"
    page = _static("index.html")
    assert ".sk" in page, "скелетон ожидания"
    assert ".offbar" in page, "плашка «нет сети»"


def test_design_css_self_hosts_own_fonts():
    css = _static("index.html")  # @font-face лежит в шапке страницы

    assert "@font-face" in css
    assert "/static/fonts/" in css
    assert "font-display:swap" in css
    for family in ("'Onest'", "'Unbounded'", "'Data'"):
        assert f"font-family:{family}" in css
    # запасные шрифты с подогнанными метриками: подмена не двигает текст
    for fallback in ("OnestFB", "UnboundedFB", "DataFB"):
        assert fallback in css
    assert "size-adjust" in css
    # ǵ в логотипе: своим файлом, иначе браузер подставит системный шрифт
    assert "unb-gacute.woff2" in css
    for f in ("onest-cyr", "onest-lat", "unb-cyr", "unb-lat", "jbm-lat"):
        assert (STATIC / "fonts" / f"{f}.woff2").exists(), f"нет файла шрифта {f}"


def test_home_keeps_api_flow_theme_and_skeleton():
    html = _static("index.html")

    assert 'id="lotUrl"' in html and 'type="url"' in html and 'inputmode="url"' in html
    assert "/api/predict" in html
    assert "localStorage" in html and "bagam-theme" in html
    # состояние ожидания: карточка мерцает, статус читается голосом
    assert "sheet.classList.add('busy')" in html
    assert 'id="checkStatus"' in html and 'aria-live="polite"' in html
    # честные тексты ошибок вместо «что-то пошло не так»
    assert "Сервис сейчас не отвечает" in html
    assert "Слишком много запросов подряд" in html
    assert "Нужна ссылка на объявление вида krisha.kz/a/show/" in html


def test_home_uses_live_sources_and_has_no_frozen_numbers():
    html = _static("index.html")

    assert "/api/stats" in html and "/api/health" in html and "/api/demo" in html
    assert "telegram-web-app.js" in html
    # цифры подставляются из API, а не живут в разметке навсегда
    for hook in ("[data-l=total]", "[data-l=ppsm]", "[data-l=mape]", "[data-l=age]"):
        assert hook in html, f"нет подстановки {hook}"
    assert "by_district" in html, "столбики районов должны перерисовываться живыми данными"
    # запас на случай молчащего API — говорим правду, а не показываем свежесть
    assert "цифры из последнего успешного обновления" in html
    assert "Нет сети" in html or "нет сети" in html


def test_home_report_shows_everything_prod_showed():
    """Редизайн не должен терять блоки, которые уже работали в проде."""
    html = _static("index.html")

    assert "renderWarn" in html and "scam_risk" in html and "duplicate_of" in html
    assert "Не вносите задаток до просмотра квартиры" in html
    assert "renderHist" in html and "price_history" in html
    assert "продавец снизил цену" in html and "продавец поднял цену" in html
    assert "renderSimilar" in html and "analogs" in html
    assert "function trackHref" in html and "?start=track_" in html
    assert "reportShareText" in html and "navigator.share" in html
    assert "Справедливая оценка: " in html and "Диапазон модели: " in html


def test_home_hides_unknown_model_features():
    html = _static("index.html")

    for key in (
        "completion_year", "apartments_count", "dist_big_road_km", "dist_industrial_km",
        "hex7_ppsm", "hex8_ppsm", "knn_n", "district_mismatch", "is_new_building",
        "security_count", "has_security_guard", "has_intercom", "has_video_surveillance",
        "category", "lat", "lon", "dist_center_km", "district_ppsm",
        "microdistrict_ppsm", "floor_ratio",
    ):
        assert f"{key}:" in html, f"нет человеческого имени для {key}"
    assert "console.warn('Неизвестный фактор скрыт:', f.feature)" in html
    assert "f.hint" in html, "подсказка модели по фактору"


def test_csp_keeps_everything_self_hosted():
    assert "default-src 'self'" in CSP
    assert "font-src 'self'" in CSP
    assert "fonts.googleapis.com" not in CSP
    assert "fonts.gstatic.com" not in CSP


def test_demo_endpoint_returns_active_listing_url_and_is_rate_limited(tmp_path, monkeypatch):
    db_path = tmp_path / "krisha.db"
    db.init_db(db_path)
    db.upsert_listing(
        {
            "id": 987654321,
            "url": "https://krisha.kz/a/show/987654321",
            "title": "Демо",
            "price": 42_000_000,
            "area": 55.0,
            "rooms": 2,
            "district": "Auezovskiy_r-n",
            "source": "test",
        },
        db_path=db_path,
    )
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    app_module._rate.clear()

    client = TestClient(app)
    resp = client.get("/api/demo", headers={"x-forwarded-for": "203.0.113.79"})
    assert resp.status_code == 200
    assert resp.json() == {
        "listing_id": 987654321,
        "url": "https://krisha.kz/a/show/987654321",
    }

    app_module._rate.clear()
    for _ in range(app_module.RATE_LIMIT):
        assert (
            client.get("/api/demo", headers={"x-forwarded-for": "203.0.113.80"}).status_code
            == 200
        )
    limited = client.get("/api/demo", headers={"x-forwarded-for": "203.0.113.80"})
    assert limited.status_code == 429


def test_home_has_no_llm_flag_hydration_left():
    """issue #157: путь LLM-бейджей убран целиком, включая догрузку на фронте."""
    html = _static("index.html")

    for leftover in ("/api/flags", "flags_pending", "text_flags",
                     "hydrateFlags", "flag-pending"):
        assert leftover not in html, f"остался хвост LLM-флагов: {leftover}"
