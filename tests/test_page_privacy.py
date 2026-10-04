"""Страница /privacy: политика конфиденциальности сайта и Telegram-бота.

Политика — это набор утверждений о коде, поэтому часть тестов сверяет её с кодом:
сроки, лимиты и команды берутся из самих модулей, а «нет cookie и сторонних
скриптов» проверяется по всем страницам сайта. Изменили код или страницы так, что
политика устарела, — тест упадёт и подскажет, что поправить в static/privacy.html.
"""

import inspect
import re
from pathlib import Path

from fastapi.testclient import TestClient

from krisha import bot, db, predict_gate, prediction_log, subscriptions, text_parse, tracking, usage
from krisha.api.app import app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

# Ключи localStorage, о которых сказано в политике. Новый ключ на любой странице
# сайта нужно сначала описать в разделе «Память браузера» на /privacy.
DOCUMENTED_STORAGE_KEYS = {"bagam-theme", "kfp-theme", "bagam-stats"}


def _page() -> str:
    return (STATIC / "privacy.html").read_text(encoding="utf-8").replace("&nbsp;", " ")


def _site_pages() -> dict[str, str]:
    """Все страницы и общий скрипт static/js/site.js — он исполняется на каждой из них."""
    pages = {p.name: p.read_text(encoding="utf-8") for p in sorted(STATIC.glob("*.html"))}
    pages["js/site.js"] = (STATIC / "js" / "site.js").read_text(encoding="utf-8")
    return pages


def _src(*parts: str) -> str:
    return ROOT.joinpath("src", "krisha", *parts).read_text(encoding="utf-8")


def test_privacy_route_serves_page_with_security_headers():
    client = TestClient(app)
    resp = client.get("/privacy")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<title>Политика конфиденциальности │ baǵam</title>" in resp.text
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp and "font-src 'self'" in csp
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert client.head("/privacy").status_code == 200


def test_privacy_is_listed_for_search_engines():
    client = TestClient(app)

    assert "/privacy</loc>" in client.get("/sitemap.xml").text
    html = _page()
    assert '<link rel="canonical" href="https://bagam.info/privacy">' in html
    assert '<meta name="description"' in html


def test_privacy_uses_shared_design_and_document_structure():
    html = _page()

    assert 'href="/static/design.css"' in html
    assert 'rel="icon" type="image/svg+xml" href="/static/favicon.svg"' in html
    assert html.count("<h1") == 1
    assert "Политика" in html.split("<h1", 1)[1].split("</h1>", 1)[0]
    # содержание и секции связаны якорями
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    anchors = re.findall(r'href="#([a-z0-9-]+)"', html)
    toc = [a for a in anchors if a not in {"main"}]
    assert len(toc) >= 9
    assert set(toc) <= ids, f"в содержании есть якоря без секций: {set(toc) - ids}"
    sections = re.findall(r'<section class="ds" id="([^"]+)">', html)
    assert sections == toc, "порядок секций и содержания должен совпадать"
    assert html.count('<h2 class="dh"') == len(sections)
    assert "m3.css" not in html and "FairPrice" not in html and "Manrope" not in html


def test_privacy_names_the_real_data_flows():
    html = _page()

    for needle in (
        "localStorage",
        "bagam-theme",
        "bagam-stats",
        "Telegram",
        "Hugging Face",
        "GitHub",
        "krisha.kz",
        "Gemini",
        "Fernet",
        "cookie",
        "номер чата",
        "приватн",
    ):
        assert needle in html, f"в политике не упомянуто: {needle}"


def test_privacy_has_edition_date_and_contacts():
    html = _page()

    assert "Редакция от" in html and "4 октября 2026" in html
    assert 'datetime="2026-10-04"' in html
    assert 'href="https://t.me/Hopepe1"' in html
    assert 'href="https://t.me/fairprice_kzbot"' in html
    assert 'href="https://github.com/Dex719/krisha-fair-price"' in html
    assert "Куда дальше" in html and 'href="/terms"' in html and 'href="/bot"' in html


def test_privacy_has_no_external_cdns_or_trackers():
    html = _page()

    for host in (
        "cdn.jsdelivr.net",
        "cdnjs.cloudflare.com",
        "unpkg.com",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "googletagmanager.com",
        "google-analytics.com",
        "mc.yandex.ru",
        "connect.facebook.net",
    ):
        assert host not in html, f"на странице политики не должно быть {host}"
    assert not re.search(r"<(?:script|img|iframe)[^>]+\bsrc=[\"']https?://", html)
    assert not re.search(r"<link[^>]+rel=[\"']stylesheet[\"'][^>]+href=[\"']https?://", html)


def test_site_pages_match_the_no_cookies_no_trackers_claims():
    """Раздел «Чего мы не собираем» держится на этих проверках."""
    for name, html in _site_pages().items():
        assert "document.cookie" not in html, f"{name}: cookie из браузера"
        assert "sessionStorage" not in html and "indexedDB" not in html, name
        assert "navigator.sendBeacon" not in html, name
        assert "geolocation" not in html and "getUserMedia" not in html, name
        assert not re.search(r"<(?:script|img|iframe)[^>]+\bsrc=[\"']https?://", html), (
            f"{name}: внешний ресурс в разметке, а политика обещает свои"
        )
        # единственный внешний скрипт — Telegram Mini App, и только внутри Telegram
        loaded = set(re.findall(r"\.src\s*=\s*'(https?://[^']+)'", html))
        assert loaded <= {"https://telegram.org/js/telegram-web-app.js"}, (name, loaded)
    assert "set_cookie" not in _src("api", "app.py")


def test_localstorage_keys_on_every_page_are_documented():
    page = _page()

    used: set[str] = set()
    for html in _site_pages().values():
        used |= set(re.findall(r"localStorage\.(?:get|set|remove)Item\(\s*['\"]([^'\"]+)['\"]", html))
    assert used <= DOCUMENTED_STORAGE_KEYS, (
        f"страницы пишут в localStorage ключи, о которых молчит /privacy: "
        f"{used - DOCUMENTED_STORAGE_KEYS}"
    )
    for key in DOCUMENTED_STORAGE_KEYS:
        assert f"<code>{key}</code>" in page


def test_policy_numbers_match_the_code():
    page = _page()

    assert usage.KEEP_DAYS == 60 and "60 дней" in page
    assert prediction_log.MAX_ROWS == 20_000 and "20 000" in page
    assert tracking.MAX_TRACKED_PER_CHAT == 10 and "10 объявлений" in page
    assert text_parse.MIN_TEXT_LEN == 40 and "не короче 40 символов" in page
    assert "[:3000]" in _src("text_parse.py") and "3000 символов" in page
    assert inspect.signature(db.remember_update_id).parameters["keep"].default == 5000
    assert "последних 5000" in page
    assert re.search(r'"PREDICT_CACHE_TTL_S",\s*600', _src("predict_gate.py"))
    assert "около 10 минут" in page
    assert re.search(r'_env_int\("RATE_LIMIT_PER_WINDOW",\s*15\)', _src("api", "app.py"))
    assert "не больше 15 оценок в минуту" in page


def test_policy_storage_claims_match_the_code(tmp_path, monkeypatch):
    page = _page()
    subs = _src("subscriptions.py")
    pushed: list[tuple[str, str]] = []

    def fake_push(path, payload, message, *args, **kwargs):
        pushed.append((message, payload))

    monkeypatch.setattr(subscriptions, "_push_to_github", fake_push)
    monkeypatch.setattr(subscriptions, "SUBSCRIPTIONS_PATH", tmp_path / "subscriptions.json")
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "privacy-test-key")

    # подписка и слежка: файл зашифрован целиком, в сообщении коммита нет номера чата
    subscriptions.set_subscription(777123, {"rooms": 2, "max_price": 45_000_000, "district": None})
    tracking.add_tracked(777123, 424242424, 45_000_000, "Лот", path=tmp_path / "tracked.json")
    assert len(pushed) == 2
    for message, payload in pushed:
        assert "777123" not in message and "424242424" not in message
        assert "_encrypted" in payload and "777123" not in payload and "424242424" not in payload
    assert "Fernet" in subs and "Fernet" in page

    # без ключа файл с номерами чатов в репозиторий не уходит вообще
    pushed.clear()
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    subscriptions.set_subscription(777123, {"rooms": 1})
    assert pushed == []

    # обезличенные файлы пишутся открыто, и политика говорит об этом
    assert "encrypt=False" in _src("usage.py") and "encrypt=False" in _src("prediction_log.py")
    assert "не шифруются" in page.lower()
    # состояние живёт в приватном репозитории
    assert re.search(r'KRISHA_STATE_REPO",\s*"Dex719/krisha-db"', subs)


def test_policy_counters_and_journal_claims_match_the_code(monkeypatch):
    page = _page()

    # хэш номера чата: 10 символов и зависит от секретной соли
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "salt-a")
    first = usage._hash_user(777123)
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", "salt-b")
    assert len(first) == 10 and first != usage._hash_user(777123)
    assert "10 символов" in page and "секретной солью" in page
    # журнал оценок по умолчанию выключен
    monkeypatch.delenv("PREDICTION_LOG", raising=False)
    assert prediction_log._enabled() is False
    assert "по умолчанию выключен" in page


def test_bot_part_reads_only_the_fields_it_claims():
    src = inspect.getsource(bot.handle_update)

    for field in ('message.get("chat"', 'message.get("text")'):
        assert field in src
    assert '"from"' not in src and "username" not in src
    assert "chat_id" in src and "record_event" in src


def test_commands_in_policy_exist_in_the_bot():
    page = _page()
    known = bot.HELP_TEXT + bot.ALERTS_HELP + bot.TRACK_HELP

    commands = set(re.findall(r"<(?:code|span class=\"k\")>(/[a-z_]+)", page))
    assert {"/alerts_off", "/alerts_on", "/track", "/untrack", "/alerts"} <= commands
    for command in commands:
        assert command in known, f"в политике команда, которой нет у бота: {command}"
    assert "/untrack all" in page and "/untrack all" in bot.TRACK_HELP.replace("<code>", "")


def test_link_is_not_kept_only_the_listing_id_is():
    """«Ссылка не сохраняется»: кэш и база работают по номеру объявления."""
    page = _page()

    assert predict_gate.cache_key("https://krisha.kz/a/show/1012607661?utm_source=x#frag") == "1012607661"
    assert predict_gate.cache_key("http://krisha.kz/a/show/1012607661") == "1012607661"
    assert "Сама ссылка не сохраняется" in page
    # в базу кладётся канонический адрес, собранный сервером из номера
    assert "KRISHA_SHOW_BASE + match.group(1)" in _src("predict.py")
    # ссылку присылают в теле POST-запроса, а не в адресе страницы
    assert '@app.post("/api/predict"' in _src("api", "app.py")
    assert "в теле запроса" in page


def test_license_named_on_the_page_matches_the_repo():
    assert "Elastic License 2.0" in (ROOT / "LICENSE").read_text(encoding="utf-8")[:80]
    assert "Elastic License 2.0" in _page()


def test_actions_log_claim_matches_the_alert_script():
    """Раздел «Кто имеет доступ» признаёт: при недоставке слежки номер чата попадает в журнал Actions."""
    script = (ROOT / "scripts" / "send_alerts.py").read_text(encoding="utf-8")
    page = _page()

    # Номер чата в публичный журнал не попадает целиком — только две последние
    # цифры (bot.mask_chat_id), и политика говорит ровно это.
    assert "Не доставлено в chat {mask_chat_id(chat_id)}" in script
    assert "{chat_id}" not in script
    from krisha.bot import mask_chat_id
    assert mask_chat_id(123456789) == "***89"
    assert "Журнал рассылки в GitHub Actions" in page
    assert "только две последние цифры номера чата" in page
    assert "номер этого чата" not in page
    # рассылка идёт из Actions, а не с сервера Space
    workflow = (ROOT / ".github" / "workflows" / "rescrape.yml").read_text(encoding="utf-8")
    assert "python -m krisha.subscriptions --pull" in workflow and "scripts/send_alerts.py" in workflow


def _section(html: str, sid: str) -> str:
    m = re.search(rf'<section class="ds" id="{sid}">.*?</section>', html, flags=re.S)
    assert m, f"нет раздела #{sid}"
    return m.group(0)


def test_operator_is_not_invented_and_owner_todo_is_left():
    """Оператор — только то, что есть в репозитории (ник автора); ФИО и контакт впишет владелец."""
    html = _page()
    operator = _section(html, "operator")

    assert 'href="https://t.me/Hopepe1"' in operator
    assert "<!-- TODO(владелец):" in operator and "ФИО" in operator
    assert "№ 94-V" in operator and "О персональных данных и их защите" in operator
    # ни выдуманных e-mail, ни ИИН
    assert not re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.sub(r"<!--.*?-->", "", html, flags=re.S))
    assert "ИИН" not in html


def test_basis_is_consent_through_bot_actions():
    basis = _section(_page(), "basis")

    assert "с вашего согласия" in basis
    assert "<code>/track</code>" in basis and "<code>/alerts_on</code>" in basis


def test_cross_border_section_names_the_real_services_and_localization_risk():
    page = _page()
    transfer = _section(page, "transfer")

    for service in ("Hugging Face", "Cloudflare", "GitHub", "Telegram", "Google"):
        assert service in transfer, f"не названа зарубежная платформа: {service}"
    assert "Статья 12" in transfer and "на территории Республики Казахстан" in transfer
    # bagam.info идёт через Cloudflare Worker, а webhook бота — мимо него, на адрес Space
    worker = (ROOT / "docs" / "domain-worker.js").read_text(encoding="utf-8")
    assert "Cloudflare Worker" in worker and "bot.webhook_base_url" in worker
    assert "SPACE_HOST" in inspect.getsource(bot.webhook_base_url)
    assert "Сообщения боту идут мимо Cloudflare" in transfer
    # Gemini — реально используемый внешний сервис
    assert "generativelanguage.googleapis.com" in _src("llm_flags.py")


def test_rights_explain_how_to_exercise_them():
    rights = _section(_page(), "rights")

    for right in ("узнать", "исправить", "удалить", "отозвать согласие"):
        assert right in rights.lower(), right
    for cmd in ("<code>/track</code>", "<code>/alerts</code>", "<code>/alerts_off</code>", "<code>/untrack all</code>"):
        assert cmd in rights
    assert "в сроки, установленные законодательством Республики Казахстан" in rights


def test_technical_details_are_collapsed():
    """Fernet, webhook, Bot API и список исходников — в свёрнутых <details>, не в основном тексте."""
    html = _page()
    tech = "".join(re.findall(r'<details class="tech">.*?</details>', html, flags=re.S))
    rest = re.sub(r'<details class="tech">.*?</details>', "", html, flags=re.S)
    main = rest[rest.index('<main id="main">'):rest.index("</main>")]

    for needle in ("Fernet", "webhook", "Bot API", "subscriptions.py", "последних 5000"):
        assert needle in tech, needle
        assert needle not in main, f"{needle} вне свёрнутых подробностей"
    assert "<details open" not in html.replace('<details class="toc"', "")
    assert "Главное за полминуты" in main
