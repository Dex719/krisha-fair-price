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


def _with_shared(name: str) -> str:
    """Страница вместе с общим кодом: тема, живые цифры, Telegram и плашка «нет сети»
    живут в static/js/site.js и подключаются на всех страницах."""
    html = _static(name)
    assert '<script src="/static/js/site.js" defer' in html, f"{name}: не подключён site.js"
    return html + _static("js/site.js")


def test_home_uses_bagam_meta_design_css_and_local_favicon():
    html = _static("index.html")

    assert (
        "<title>Оценка квартиры в Алматы: справедливая цена продажи и аренды │ baǵam</title>"
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
    assert ".sk{" in css, "скелетон ожидания"
    assert ".offbar{" in css, "плашка «нет сети»"
    assert ".sheet[aria-busy=true]" in _static("index.html"), "скелетон разбора — только у главной"


def test_design_css_self_hosts_own_fonts():
    css = _static("design.css")  # @font-face — один раз в общем файле, а не копией в каждой странице

    assert "@font-face" in css
    assert "/static/fonts/" in css
    assert "font-display:swap" in css
    for family in ("'Onest'", "'Unbounded'"):
        assert f"font-family:{family}" in css
    # запасные шрифты с подогнанными метриками: подмена не двигает текст
    for fallback in ("OnestFB", "UnboundedFB"):
        assert fallback in css
    assert "size-adjust" in css
    # ǵ в логотипе: своим файлом, иначе браузер подставит системный шрифт
    assert "unb-gacute.woff2" in css
    for f in ("onest-cyr", "onest-lat", "unb-cyr", "unb-lat"):
        assert (STATIC / "fonts" / f"{f}.woff2").exists(), f"нет файла шрифта {f}"
    # моноширинный шрифт ушёл: подписи и цифры — Onest с tabular-nums, лишнего файла нет
    assert "jbm-lat" not in css and not (STATIC / "fonts" / "jbm-lat.woff2").exists()
    assert "JetBrains" not in css and "monospace" not in css
    assert "font-variant-numeric:tabular-nums" in css


def test_home_keeps_api_flow_theme_and_skeleton():
    html = _static("index.html")

    assert 'id="lotUrl"' in html and 'type="url"' in html and 'inputmode="url"' in html
    assert "/api/predict" in html
    assert "localStorage" in html and "bagam-theme" in html
    # состояние ожидания: карточка мерцает (aria-busy), статус — над вердиктом и голосом
    assert "sheet.setAttribute('aria-busy'" in html and 'id="rWait"' in html
    assert html.index('id="rWait"') < html.index('class="verdict')
    assert 'id="checkStatus"' in html and 'aria-live="polite"' in html
    # дольше 45 секунд не ждём: запрос обрывается, человек видит понятную ошибку
    assert "new AbortController()" in html and "LIMIT = 45000" in html
    assert "Оценка не пришла за 45 секунд" in html
    # подсказка «оценку пришлёт бот» на долгом ожидании больше не уводит со страницы
    assert "оценку пришлёт бот" not in html
    # честные тексты ошибок вместо «что-то пошло не так»
    assert "Сервис сейчас не отвечает" in html
    assert "Слишком много запросов подряд" in html
    assert "Нужна ссылка на объявление вида krisha.kz/a/show/" in html


def test_home_uses_live_sources_and_has_no_frozen_numbers():
    html = _with_shared("index.html")

    assert "/api/stats" in html and "/api/health" in html and "/api/demo" in html
    assert "telegram-web-app.js" in html
    # цифры подставляются из API, а не живут в разметке навсегда
    for key in ("total", "ppsm", "mape", "age"):
        assert f"put('{key}'" in html, f"нет подстановки [data-l={key}]"
        assert f'data-l="{key}"' in _static("index.html"), f"на главной нет места для [data-l={key}]"
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
    # «продавец/арендодатель снизил цену» — кто именно, зависит от сделки
    assert "'продавец'" in html and "'арендодатель'" in html
    assert " снизил цену на " in html and " поднял цену на " in html
    assert "renderSimilar" in html and "analogs" in html
    assert "function trackHref" in html and "?start=track_" in html
    # «Поделиться» пока «скоро» — см. test_home_polish
    assert '<em class="soon">скоро</em>' in html


def _names_block(html: str) -> str:
    start = html.index("const NAMES = {")
    return html[start:html.index("};", start)]


def test_home_hides_unknown_model_features():
    html = _static("index.html")

    assert "console.warn('Неизвестный фактор скрыт:', f.feature)" in html
    assert "f.hint" in html, "подсказка модели по фактору"


def test_home_names_every_factor_the_api_can_return_and_no_aliases():
    """API сливает коллинеарные признаки (predict.FACTOR_GROUPS) — у каждого фактора,
    который может прийти, есть подпись, а у слитых членов групп подписей-дублей больше нет."""
    from krisha import features
    from krisha.predict import _FEATURE_GROUP

    names = _names_block(_static("index.html"))
    every = features.ALL_FEATURES + features.RENT_NUM_FEATURES + features.RENT_CAT_FEATURES
    keys = {_FEATURE_GROUP.get(f, f) for f in every}
    for key in sorted(keys):
        assert f"{key}:" in names, f"нет человеческого имени для фактора {key}"
    for alias in sorted(set(_FEATURE_GROUP) - set(_FEATURE_GROUP.values())):
        assert f" {alias}:" not in names and f",{alias}:" not in names, f"алиас слитого признака: {alias}"
    for stale in ("house_age", "ceiling_height", "dist_center:", "district_median_ppsm",
                  "micro_median_ppsm", "knn_n"):
        assert stale not in names, f"признака {stale} модель не отдаёт"


def test_home_example_report_is_marked_and_honest():
    """Пример разбора не выдаёт себя за настоящий результат и не «дорастает» из нуля."""
    html = _static("index.html")

    assert 'class="sheet is-demo"' in html and 'id="rTag"' in html and "<b>Пример</b>" in html
    assert "обновлено сегодня" not in html
    assert 'id="count">27 500 000' in html, "цифры примера — сразу в разметке"
    assert "data-target" not in html and "window.__live" not in html
    # ошибка — внутри карточки: что случилось и что сделать
    assert 'id="rErr"' in html and "data-retry" in html


def test_home_verdict_explains_itself_and_flags_scam():
    html = _static("index.html")

    # вердикт ставится по обычному разбросу — строка под ним объясняет почему
    assert "Обычный разброс для таких квартир — от " in html and "в него укладывается." in html
    # сильно ниже рынка — не зелёное «Выгодно», а предупреждение без процента
    assert "Подозрительно дёшево" in html and "r.scam_risk.level === 'high'" in html
    # поделиться и слежение — кнопками сразу под вердиктом
    assert html.index('class="verdict') < html.index('id="rActs"') < html.index('class="rnums"')
    assert 'class="rbtn rshare"' in html and 'class="rbtn rtrack"' in html


def test_home_report_escapes_listing_text_and_checks_urls():
    """title, address, url и подписи приходят от krisha — в innerHTML только через esc."""
    html = _static("index.html")

    assert "esc(r.address)" in html and "esc(t)" in html
    assert "' + r.url + '" not in html and "' + r.address + '" not in html
    assert "function lotUrl" in html and "/^https:\\/\\/(www\\.)?krisha\\.kz\\//i" in html
    assert "esc(name)" in html, "подпись фактора тоже экранируется: в ней значения из объявления"


def test_home_dropped_blocks_stay_gone():
    """Манифест, панорама между формой и разбором, «Куда дальше», декоративная карта."""
    html = _static("index.html")

    for gone in ('class="statement"', 'id="fillTxt"', 'class="cityband"', 'id="cityimg"',
                 "Куда дальше", 'class="ncards"', 'class="hexmap"', "ledetag", "как это работает",
                 ".chat .msg", ".fxb i.pos", "nidx", "гексагональн", "гейт", "свежем тесте"):
        assert gone not in html, f"вернулось удалённое: {gone}"
    # тексты, по которым ходят e2e и скриншоты
    assert ">пример продажи<" in html and ">пример аренды<" in html


def test_home_district_bars_use_honest_zero_based_scale():
    html = _static("index.html")

    assert "v/mx*100" in html, "высота столбика = цена / максимум"
    assert "12+((" not in html, "старая min-max нормализация с полом 12%"


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

    # У демо свой бакет и свой потолок: его дёргает каждая загрузка главной,
    # а за одним мобильным IP в Казахстане сидит пол-города (CGNAT). Строгий
    # лимит предиктов здесь ломал бы кнопку живым людям.
    app_module._rate.clear()
    monkeypatch.setattr(app_module, "DEMO_RATE_LIMIT", 4)
    for _ in range(app_module.DEMO_RATE_LIMIT):
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


def test_demo_endpoint_serves_rent_listing_from_rent_base(tmp_path, monkeypatch):
    """?deal=arenda — пример берётся из базы аренды; без неё честный 503."""
    rent_path = tmp_path / "krisha_rent.db"
    db.init_db(rent_path)
    db.upsert_listing(
        {"id": 555000111, "url": "https://krisha.kz/a/show/555000111", "title": "Аренда",
         "price": 300_000, "area": 50.0, "rooms": 2, "district": "Auezovskiy_r-n", "source": "test"},
        db_path=rent_path, price_bounds=(20_000, 10_000_000),
    )
    monkeypatch.setattr(app_module, "RENT_DB_PATH", rent_path)
    app_module._rate.clear()
    client = TestClient(app)

    resp = client.get("/api/demo?deal=arenda", headers={"x-forwarded-for": "203.0.113.81"})
    assert resp.status_code == 200
    assert resp.json() == {"listing_id": 555000111, "url": "https://krisha.kz/a/show/555000111"}

    monkeypatch.setattr(app_module, "RENT_DB_PATH", tmp_path / "missing.db")
    app_module._demo_pool_cache.clear()
    assert client.get("/api/demo?deal=arenda", headers={"x-forwarded-for": "203.0.113.82"}).status_code == 503
    assert client.get("/api/demo?deal=bogus", headers={"x-forwarded-for": "203.0.113.83"}).status_code == 422
