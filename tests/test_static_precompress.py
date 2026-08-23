"""Статика, сжатая один раз при старте: варианты, ETag, 304, HEAD.

Регрессии, которые здесь ловятся:
* сжатый и несжатый варианты обязаны иметь РАЗНЫЕ ETag (иначе кэш отдаст
  клиенту представление, которого он не просил);
* Vary: Accept-Encoding нужен и на 200, и на 304;
* ETag считается от содержимого, а не от mtime (иначе каждый деплой
  инвалидирует кэш браузера, потому что mtime = время сборки образа);
* gzip-байты детерминированы (у двух воркеров одинаковый ETag).
"""

import gzip
import os
import time

from fastapi.testclient import TestClient

from krisha.api import static_cache
from krisha.api.app import STATIC_DIR, app

RAW = {"accept-encoding": "identity"}
GZ = {"accept-encoding": "gzip, deflate"}


def _client() -> TestClient:
    return TestClient(app)


def test_index_is_served_gzipped_from_memory():
    resp = _client().get("/", headers=GZ)

    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in resp.headers["vary"]
    assert resp.headers["cache-control"] == "no-cache"
    # httpx распаковывает сам — сравниваем с файлом на диске
    assert resp.content == (STATIC_DIR / "index.html").read_bytes()


def test_index_without_gzip_is_served_raw():
    resp = _client().get("/", headers=RAW)

    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    raw = (STATIC_DIR / "index.html").read_bytes()
    assert resp.content == raw
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
    assert int(resp.headers["content-length"]) == (STATIC_DIR / "index.html").stat().st_size


def test_404_page_is_precompressed_too():
    resp = _client().get("/nope-nope", headers={**GZ, "accept": "text/html"})

    assert resp.status_code == 404
    assert resp.headers["content-encoding"] == "gzip"
    assert resp.content == (STATIC_DIR / "404.html").read_bytes()


def test_css_is_served_from_memory_and_images_stay_immutable():
    client = _client()
    css = client.get("/static/design.css", headers=GZ)
    assert css.status_code == 200
    assert css.headers["content-encoding"] == "gzip"
    assert css.headers["cache-control"] == "no-cache"
    assert css.content == (STATIC_DIR / "design.css").read_bytes()

    image = client.get("/static/img/city-860.webp")
    assert image.status_code == 200
    assert "immutable" in image.headers["cache-control"]
    # уже сжатый формат вторично не жмём
    assert "content-encoding" not in image.headers


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
