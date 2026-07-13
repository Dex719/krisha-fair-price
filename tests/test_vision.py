"""Тесты оценки ремонта по фото."""

from krisha import vision

LISTING = {"id": 777, "photos": ["https://photos.kz/1.jpg", "https://photos.kz/2.jpg"]}


def test_no_photos_or_id():
    assert vision.assess_renovation({"id": 1, "photos": []}) is None
    assert vision.assess_renovation({"photos": ["https://x/1.jpg"]}) is None


def test_photo_url_allowlist_blocks_ssrf():
    assert vision._allowed_photo_url("https://krisha-photos.kcdn.online/aa/1.jpg")
    assert not vision._allowed_photo_url("http://krisha-photos.kcdn.online/aa/1.jpg")
    assert not vision._allowed_photo_url("https://127.0.0.1/admin")
    assert not vision._allowed_photo_url("https://kcdn.online.evil.example/1.jpg")
    assert not vision._allowed_photo_url("https://user@krisha-photos.kcdn.online/1.jpg")


def test_photo_download_stops_at_byte_limit(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield b"x" * vision.MAX_PHOTO_BYTES
            yield b"overflow"

    monkeypatch.setattr(vision.httpx, "stream", lambda *args, **kwargs: FakeResponse())
    urls = ["https://krisha-photos.kcdn.online/aa/1.jpg"]
    assert vision._download_photos(urls) == []


def test_cache_roundtrip(tmp_path, monkeypatch):
    db = tmp_path / "krisha.db"
    monkeypatch.setattr(vision, "DB_PATH", db)
    import sqlite3
    sqlite3.connect(db).close()  # пустая база, таблица создастся сама

    assert vision.get_cached(777, LISTING["photos"]) is None
    vision.save_cache(777, LISTING["photos"], "good", "аккуратная кухня")
    got = vision.get_cached(777, LISTING["photos"])
    assert got == {"level": "good", "label": "хороший ремонт", "comment": "аккуратная кухня"}
    # смена набора фото инвалидирует кэш
    assert vision.get_cached(777, ["https://photos.kz/other.jpg"]) is None


def test_assess_uses_cache_without_key(tmp_path, monkeypatch):
    db = tmp_path / "krisha.db"
    monkeypatch.setattr(vision, "DB_PATH", db)
    import sqlite3
    sqlite3.connect(db).close()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    vision.save_cache(777, LISTING["photos"], "premium", "свежий ремонт")
    assert vision.assess_renovation(LISTING)["level"] == "premium"
    # без кэша и без ключа — None даже при live
    assert vision.assess_renovation({"id": 5, "photos": ["https://x/1.jpg"]}) is None


def test_assess_live_path(tmp_path, monkeypatch):
    db = tmp_path / "krisha.db"
    monkeypatch.setattr(vision, "DB_PATH", db)
    import sqlite3
    sqlite3.connect(db).close()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(vision, "_download_photos",
                        lambda photos: [("image/jpeg", b"fake")])
    monkeypatch.setattr(vision, "_gemini_assess",
                        lambda images, key: {"level": "dated", "comment": "старый ремонт"})

    got = vision.assess_renovation(LISTING)
    assert got["label"] == "жилой, но устаревший"
    # второй вызов идёт из кэша (живой запрос выключаем — не должен понадобиться)
    monkeypatch.setattr(vision, "_gemini_assess", lambda images, key: None)
    assert vision.assess_renovation(LISTING)["level"] == "dated"
    # live=False с кэшем работает, без кэша — None
    assert vision.assess_renovation(LISTING, live=False)["level"] == "dated"
    assert vision.assess_renovation({"id": 9, "photos": ["https://x/9.jpg"]}, live=False) is None
