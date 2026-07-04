"""Тесты скачивания базы и моделей из GitHub Release."""

import gzip
import io
import tarfile
from contextlib import contextmanager

import pytest

import krisha.db_release as db_release


@pytest.fixture()
def auto_on(monkeypatch):
    """conftest выключает автоскачивание — здесь включаем обратно."""
    monkeypatch.setenv("KRISHA_DB_AUTO", "1")


@pytest.fixture()
def models_auto_on(monkeypatch):
    monkeypatch.setenv("KRISHA_MODEL_AUTO", "1")


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
    # checksum-файла в релизе «нет» — проверка должна молча пропуститься
    # (и юнит-тест не должен ходить в сеть за <asset>.sha256)
    monkeypatch.setattr(
        db_release, "_verify_checksum", lambda gz_path, url: None
    )
    db = tmp_path / "krisha.db"
    assert db_release.download(db) is True
    assert db.read_bytes() == payload


def test_verify_checksum_mismatch(tmp_path, monkeypatch):
    """Несовпадение sha256 — ValueError; совпадение — тишина."""
    gz = tmp_path / "krisha.db.gz"
    gz.write_bytes(b"data")
    import hashlib

    good = hashlib.sha256(b"data").hexdigest()

    class FakeResp:
        def __init__(self, text):
            self.status_code = 200
            self.text = text

    monkeypatch.setattr(
        db_release.httpx, "get", lambda url, **kw: FakeResp(good)
    )
    db_release._verify_checksum(gz, "https://example/db.gz")  # не бросает

    monkeypatch.setattr(
        db_release.httpx, "get", lambda url, **kw: FakeResp("0" * 64)
    )
    with pytest.raises(ValueError):
        db_release._verify_checksum(gz, "https://example/db.gz")


# --- модели ---------------------------------------------------------------


def test_model_url_env_override(monkeypatch):
    monkeypatch.setenv("KRISHA_MODEL_URL", "https://example.com/models.tar.gz")
    assert db_release.model_url() == "https://example.com/models.tar.gz"


def test_ensure_models_skips_when_present(tmp_path, monkeypatch, models_auto_on):
    model_path = tmp_path / "model.cbm"
    model_path.write_bytes(b"data")
    monkeypatch.setattr(db_release, "MODEL_PATH", model_path)
    monkeypatch.setattr(
        db_release, "download_models", lambda *a, **kw: pytest.fail("не должен скачивать")
    )
    assert db_release.ensure_models() is False


def test_ensure_models_downloads_when_missing(tmp_path, monkeypatch, models_auto_on):
    monkeypatch.setattr(db_release, "MODEL_PATH", tmp_path / "model.cbm")
    called = []
    monkeypatch.setattr(
        db_release, "download_models", lambda models_dir: called.append(models_dir) or True
    )
    assert db_release.ensure_models() is True
    assert called == [db_release.MODELS_DIR]


def test_ensure_models_swallows_network_errors(tmp_path, monkeypatch, models_auto_on):
    monkeypatch.setattr(db_release, "MODEL_PATH", tmp_path / "model.cbm")

    def boom(models_dir):
        raise OSError("network down")

    monkeypatch.setattr(db_release, "download_models", boom)
    assert db_release.ensure_models() is False  # не роняет приложение


def test_ensure_models_respects_auto_off(tmp_path, monkeypatch):
    monkeypatch.setenv("KRISHA_MODEL_AUTO", "0")
    monkeypatch.setattr(db_release, "MODEL_PATH", tmp_path / "model.cbm")
    monkeypatch.setattr(
        db_release, "download_models", lambda *a, **kw: pytest.fail("не должен скачивать")
    )
    assert db_release.ensure_models() is False


def _make_models_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def test_download_models_extracts_tar_atomically(tmp_path, monkeypatch):
    files = {"model.cbm": b"MODEL", "model_meta.json": b'{"mape": 9.9}'}
    tar_bytes = _make_models_tar(files)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield tar_bytes

    @contextmanager
    def fake_stream(method, url, **kwargs):
        assert method == "GET"
        yield FakeResponse()

    monkeypatch.setattr(db_release.httpx, "stream", fake_stream)
    monkeypatch.setattr(db_release, "_verify_checksum", lambda tar_path, url: None)

    models_dir = tmp_path / "models"
    assert db_release.download_models(models_dir) is True
    assert (models_dir / "model.cbm").read_bytes() == b"MODEL"
    assert (models_dir / "model_meta.json").read_bytes() == b'{"mape": 9.9}'


def test_download_models_rejects_path_traversal(tmp_path, monkeypatch):
    tar_bytes = _make_models_tar({"../evil.cbm": b"x"})

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield tar_bytes

    @contextmanager
    def fake_stream(method, url, **kwargs):
        yield FakeResponse()

    monkeypatch.setattr(db_release.httpx, "stream", fake_stream)
    monkeypatch.setattr(db_release, "_verify_checksum", lambda tar_path, url: None)

    with pytest.raises(ValueError):
        db_release.download_models(tmp_path / "models")
