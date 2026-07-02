"""Тесты скачивания базы из GitHub Release."""

import gzip
from contextlib import contextmanager

import pytest

import krisha.db_release as db_release


@pytest.fixture()
def auto_on(monkeypatch):
    """conftest выключает автоскачивание — здесь включаем обратно."""
    monkeypatch.setenv("KRISHA_DB_AUTO", "1")


def test_db_url_env_override(monkeypatch):
    monkeypatch.setenv("KRISHA_DB_URL", "https://example.com/x.gz")
    assert db_release.db_url() == "https://example.com/x.gz"


def test_ensure_db_skips_when_present(tmp_path, monkeypatch, auto_on):
    db = tmp_path / "krisha.db"
    db.write_bytes(b"data")
    monkeypatch.setattr(db_release, "DB_PATH", db)
    monkeypatch.setattr(db_release, "download", lambda *a: pytest.fail("не должен скачивать"))
    assert db_release.ensure_db() is False


def test_ensure_db_downloads_when_missing(tmp_path, monkeypatch, auto_on):
    db = tmp_path / "krisha.db"
    monkeypatch.setattr(db_release, "DB_PATH", db)
    called = []
    monkeypatch.setattr(db_release, "download", lambda path: called.append(path) or True)
    assert db_release.ensure_db() is True
    assert called == [db]


def test_ensure_db_swallows_network_errors(tmp_path, monkeypatch, auto_on):
    monkeypatch.setattr(db_release, "DB_PATH", tmp_path / "krisha.db")

    def boom(path):
        raise OSError("network down")

    monkeypatch.setattr(db_release, "download", boom)
    assert db_release.ensure_db() is False  # не роняет приложение


def test_ensure_db_respects_auto_off(tmp_path, monkeypatch):
    monkeypatch.setenv("KRISHA_DB_AUTO", "0")
    monkeypatch.setattr(db_release, "DB_PATH", tmp_path / "krisha.db")
    monkeypatch.setattr(db_release, "download", lambda *a: pytest.fail("не должен скачивать"))
    assert db_release.ensure_db() is False


def test_download_unpacks_gzip(tmp_path, monkeypatch):
    payload = b"SQLite format 3\x00" + b"x" * 100
    gz_bytes = gzip.compress(payload)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield gz_bytes

    @contextmanager
    def fake_stream(method, url, **kwargs):
        assert method == "GET"
        yield FakeResponse()

    monkeypatch.setattr(db_release.httpx, "stream", fake_stream)
    db = tmp_path / "krisha.db"
    assert db_release.download(db) is True
    assert db.read_bytes() == payload
