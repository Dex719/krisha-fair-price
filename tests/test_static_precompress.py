"""Статика, сжатая один раз при старте: варианты, ETag, 304, HEAD.

Регрессии, которые здесь ловятся:
* сжатый и несжатый варианты обязаны иметь РАЗНЫЕ ETag (иначе кэш отдаст
  клиенту представление, которого он не просил);
* Vary: Accept-Encoding нужен и на 200, и на 304;
* ETag считается от содержимого, а не от mtime (иначе каждый деплой
  инвалидирует кэш браузера, потому что mtime = время сборки образа);
* gzip-байты детерминированы (у двух воркеров одинаковый ETag);
* ссылки на ассеты в страницах — с ?v=<хэш содержимого>: с версией ассет
  immutable на год, без неё (или со старой) — no-cache.
"""

import gzip
import os
import re
import time

import pytest
from fastapi.testclient import TestClient

from krisha.api import app as app_module
from krisha.api import static_cache
from krisha.api.app import STATIC_DIR, app

RAW = {"accept-encoding": "identity"}
GZ = {"accept-encoding": "gzip, deflate"}
IMMUTABLE = "public, max-age=31536000, immutable"
_VERSION_RE = re.compile(rb"\?v=[0-9a-f]{%d}" % static_cache.VERSION_LEN)


def _client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _pages_as_files():
    """Сравнение с файлом на диске — без живого снимка данных (live_pages), как
    сразу после импорта: снимок кладут в разметку тесты, поднимающие приложение."""
    app_module._render_pages(None)
    yield
    app_module._render_pages(app_module._LIVE)


def _disk(name: str) -> bytes:
    return (STATIC_DIR / name).read_bytes()


def _unversioned(body: bytes) -> bytes:
    """Отдаваемая страница = файл на диске + ?v= у ссылок на ассеты."""
    return _VERSION_RE.sub(b"", body)


def test_index_is_served_gzipped_from_memory():
    resp = _client().get("/", headers=GZ)

    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in resp.headers["vary"]
    assert resp.headers["cache-control"] == "no-cache"
    # httpx распаковывает сам — сравниваем с файлом на диске
    assert _unversioned(resp.content) == _disk("index.html")
    assert resp.content == app_module._ASSETS["index.html"].raw


def test_index_without_gzip_is_served_raw():
    resp = _client().get("/", headers=RAW)

    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    raw = app_module._ASSETS["index.html"].raw
    assert resp.content == raw
    assert _unversioned(raw) == _disk("index.html")
    assert int(resp.headers["content-length"]) == len(raw)


def test_variants_have_different_etags():
    client = _client()
    gz_etag = client.get("/", headers=GZ).headers["etag"]
    raw_etag = client.get("/", headers=RAW).headers["etag"]

    assert gz_etag != raw_etag
    assert gz_etag.endswith('-gz"')


def test_conditional_request_returns_304_with_vary():
    client = _client()
    etag = client.get("/", headers=GZ).headers["etag"]

    resp = client.get("/", headers={**GZ, "if-none-match": etag})

    assert resp.status_code == 304
    assert resp.content == b""
    assert "Accept-Encoding" in resp.headers["vary"]
    assert resp.headers["etag"] == etag


def test_etag_of_other_variant_does_not_match():
    """ETag сжатого варианта не должен давать 304 клиенту без gzip."""
    client = _client()
    gz_etag = client.get("/", headers=GZ).headers["etag"]

    resp = client.get("/", headers={**RAW, "if-none-match": gz_etag})

    assert resp.status_code == 200


def test_head_request_has_headers_without_body():
    resp = _client().head("/", headers=RAW)

    assert resp.status_code == 200
    assert resp.content == b""
    assert int(resp.headers["content-length"]) == len(app_module._ASSETS["index.html"].raw)


def test_404_page_is_precompressed_too():
    resp = _client().get("/nope-nope", headers={**GZ, "accept": "text/html"})

    assert resp.status_code == 404
    assert resp.headers["content-encoding"] == "gzip"
    assert _unversioned(resp.content) == _disk("404.html")


def test_css_is_served_from_memory_and_immutable_only_with_current_version():
    client = _client()
    version = app_module._VERSIONS["design.css"]

    css = client.get(f"/static/design.css?v={version}", headers=GZ)
    assert css.status_code == 200
    assert css.headers["content-encoding"] == "gzip"
    assert css.headers["cache-control"] == IMMUTABLE
    # шрифты внутри css тоже с версией — иначе @font-face и preload разойдутся
    assert _unversioned(css.content) == _disk("design.css")
    # версия — от ОТДАВАЕМЫХ байтов (с версиями шрифтов внутри)
    assert version == static_cache.content_version(css.content)

    # без версии или со старой — браузер перепроверит по ETag
    assert client.get("/static/design.css", headers=GZ).headers["cache-control"] == "no-cache"
    stale = client.get("/static/design.css?v=0123456789", headers=GZ)
    assert stale.status_code == 200
    assert stale.headers["cache-control"] == "no-cache"


def test_images_are_immutable_only_with_current_version():
    """svg/webp раньше были immutable без версии в URL: поменянный skyline.svg
    вернувшиеся пользователи не видели год."""
    client = _client()
    version = app_module._VERSIONS["img/city-1400.webp"]

    image = client.get(f"/static/img/city-1400.webp?v={version}")
    assert image.status_code == 200
    assert image.headers["cache-control"] == IMMUTABLE
    # уже сжатый формат вторично не жмём
    assert "content-encoding" not in image.headers
    assert version == static_cache.content_version(_disk("img/city-1400.webp"))

    plain = client.get("/static/img/city-1400.webp")
    assert plain.headers["cache-control"] == "no-cache"
    # перепроверка дешёвая: 304 без тела, заголовок кэша на месте
    again = client.get("/static/img/city-1400.webp", headers={"if-none-match": plain.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-cache"

    svg_version = app_module._VERSIONS["img/skyline.svg"]
    svg = client.get(f"/static/img/skyline.svg?v={svg_version}", headers=GZ)
    assert svg.status_code == 200
    assert svg.headers["cache-control"] == IMMUTABLE
    assert client.get("/static/img/skyline.svg").headers["cache-control"] == "no-cache"


def test_pages_link_every_known_asset_with_its_current_version():
    """Механизм общий: версию получает любой файл static/ с подходящим
    расширением, списков конкретных имён нет."""
    html = _client().get("/", headers=RAW).text

    refs = re.findall(r"/static/([A-Za-z0-9_\-./]+)(\?v=[0-9a-f]+)?", html)
    assert refs, "на главной нет ни одной ссылки на /static/"
    for name, version in refs:
        if name in app_module._VERSIONS:
            assert version == f"?v={app_module._VERSIONS[name]}", name
    assert f'/static/design.css?v={app_module._VERSIONS["design.css"]}"' in html


def test_public_base_url_replaces_site_origin_in_pages(monkeypatch):
    """canonical/og:url/og:image захардкожены в HTML; PUBLIC_BASE_URL при
    сборке кэша их подменяет — страницы и sitemap объявляют один домен."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://staging.example/")
    try:
        app_module._build_assets()
        html = _client().get("/", headers=RAW).text
        sitemap = _client().get("/sitemap.xml").text
    finally:
        monkeypatch.undo()
        app_module._build_assets()

    assert 'rel="canonical" href="https://staging.example/"' in html
    assert "https://bagam.info" not in html
    assert "<loc>https://staging.example/</loc>" in sitemap


def test_binary_static_has_real_type_and_is_not_gzipped():
    """static-binary-headers: python:3.11-slim без /etc/mime.types не знает
    webp/woff2 — прод отдавал их как application/octet-stream при nosniff, а
    starlette 1.3.1 ещё и жала их gzip-ом. Тип и отказ от gzip не должны зависеть
    ни от таблицы MIME платформы, ни от версии starlette."""
    client = _client()
    for path, media_type in (
        ("/static/img/city-1400.webp", "image/webp"),
        ("/static/fonts/onest-cyr.woff2", "font/woff2"),
        ("/static/apple-touch-icon.png", "image/png"),
        ("/static/img/og.jpg", "image/jpeg"),
    ):
        response = client.get(path, headers=GZ)
        assert response.status_code == 200, path
        assert response.headers["content-type"] == media_type, path
        assert "content-encoding" not in response.headers, path
        assert response.content == (STATIC_DIR / path.removeprefix("/static/")).read_bytes()


def test_pages_do_not_touch_the_disk_per_request(monkeypatch):
    """Смысл всей затеи: запрос страницы — это отдача байтов из памяти."""
    calls: list[str] = []
    original = os.stat

    def counting_stat(path, *args, **kwargs):
        if str(path).endswith("index.html"):
            calls.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", counting_stat)
    client = _client()
    for _ in range(5):
        client.get("/", headers=GZ)

    assert calls == []


# -------------------------------------------------------------- модуль сам
def test_gzip_bytes_are_deterministic(tmp_path):
    path = tmp_path / "a.html"
    path.write_text("<h1>привет</h1>" * 200, encoding="utf-8")

    first = static_cache.build_asset(path)
    time.sleep(0.01)
    os.utime(path, (0, 0))  # другой mtime — байты обязаны совпасть
    second = static_cache.build_asset(path)

    assert first.gz == second.gz
    assert first.etag == second.etag
    assert gzip.decompress(first.gz) == path.read_bytes()


def test_etag_follows_content_not_mtime(tmp_path):
    path = tmp_path / "a.css"
    path.write_text("body{color:red}" * 100, encoding="utf-8")
    before = static_cache.build_asset(path).etag

    os.utime(path, (1, 1))
    assert static_cache.build_asset(path).etag == before

    path.write_text("body{color:blue}" * 100, encoding="utf-8")
    assert static_cache.build_asset(path).etag != before


def test_small_files_are_not_compressed(tmp_path):
    path = tmp_path / "tiny.html"
    path.write_text("<p>ок</p>", encoding="utf-8")

    asset = static_cache.build_asset(path)

    assert asset.gz is None
    assert asset.etag_gz is None


def test_etag_matches_handles_lists_and_weak_tags():
    assert static_cache.etag_matches('W/"abc", "def"', '"def"')
    assert static_cache.etag_matches("*", '"def"')
    assert not static_cache.etag_matches('"abc"', '"def"')
    assert not static_cache.etag_matches(None, '"def"')


def _write(root, files: dict) -> None:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))


def test_build_site_versions_follow_served_content(tmp_path):
    _write(tmp_path, {
        "page.html": '<link rel="stylesheet" href="/static/a.css">'
                     '<img src="/static/img/x.webp" srcset="/static/img/x.webp 1x">'
                     '<script>var p = "/static/img/" + n; var q = "/static/a.css?v=old";</script>',
        "a.css": "@font-face{src:url(/static/f.woff2) format('woff2')}" * 30,
        "f.woff2": b"\x00font-v1",
        "img/x.webp": b"RIFF-webp-1",
    })
    site = static_cache.build_site(tmp_path, ["a.css", "page.html"])
    html = site.assets["page.html"].raw.decode()
    css = site.assets["a.css"].raw.decode()

    assert f'/static/a.css?v={site.versions["a.css"]}"' in html
    assert html.count(f'/static/img/x.webp?v={site.versions["img/x.webp"]}') == 2
    # собранный по кусочкам путь и уже версионированная ссылка — как были
    assert '"/static/img/" + n' in html and '"/static/a.css?v=old"' in html
    assert f"url(/static/f.woff2?v={site.versions['f.woff2']})" in css
    assert site.versions["a.css"] == static_cache.content_version(site.assets["a.css"].raw)

    # поменялся шрифт → новая версия шрифта → новая версия css → новая страница
    _write(tmp_path, {"f.woff2": b"\x00font-v2"})
    site2 = static_cache.build_site(tmp_path, ["a.css", "page.html"])
    assert site2.versions["f.woff2"] != site.versions["f.woff2"]
    assert site2.versions["a.css"] != site.versions["a.css"]
    assert site2.versions["img/x.webp"] == site.versions["img/x.webp"]
    assert site2.assets["page.html"].etag != site.assets["page.html"].etag


def test_build_site_replaces_origin_only_when_public_url_is_set(tmp_path):
    page = ('<link rel="canonical" href="https://bagam.info/stats">'
            '<a href="https://bagam.info.example/">x</a>')
    _write(tmp_path, {"p.html": page})

    same = static_cache.build_site(tmp_path, ["p.html"], origins=("https://bagam.info",))
    moved = static_cache.build_site(
        tmp_path, ["p.html"], origins=("https://bagam.info",), public_origin="https://new.example/"
    )

    assert same.assets["p.html"].raw.decode() == page
    out = moved.assets["p.html"].raw.decode()
    assert 'href="https://new.example/stats"' in out
    # чужой домен с тем же префиксом не задет
    assert "https://bagam.info.example/" in out
