"""Тесты скачивания базы и моделей из релизов (приватного) репозитория данных."""

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

    monkeypatch.setenv("KRISHA_DB_URL", "https://example.com/krisha.db.gz")
    monkeypatch.setattr(db_release.httpx, "stream", fake_stream)
    # checksum-файла в релизе «нет» — проверка должна молча пропуститься
    # (и юнит-тест не должен ходить в сеть за <asset>.sha256)
    monkeypatch.setattr(db_release, "_verify_checksum", lambda *a, **kw: None)
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
    db_release._verify_checksum(gz, "https://example/db.gz.sha256")  # не бросает

    monkeypatch.setattr(
        db_release.httpx, "get", lambda url, **kw: FakeResp("0" * 64)
    )
    with pytest.raises(ValueError):
        db_release._verify_checksum(gz, "https://example/db.gz.sha256")

    # нет ассета с контрольной суммой — проверка пропускается, в сеть не ходим
    monkeypatch.setattr(db_release.httpx, "get", lambda *a, **kw: pytest.fail("сеть"))
    db_release._verify_checksum(gz, None)


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

    monkeypatch.setenv("KRISHA_MODEL_URL", "https://example.com/models.tar.gz")
    monkeypatch.setattr(db_release.httpx, "stream", fake_stream)
    monkeypatch.setattr(db_release, "_verify_checksum", lambda *a, **kw: None)

    models_dir = tmp_path / "models"
    assert db_release.download_models(models_dir) is True
    assert (models_dir / "model.cbm").read_bytes() == b"MODEL"
    assert (models_dir / "model_meta.json").read_bytes() == b'{"mape": 9.9}'


def test_download_models_stages_inside_models_dir(tmp_path, monkeypatch):
    """В образе Space /app (родитель models/) принадлежит root: временный
    каталог рядом с models/ не создаётся, и прод вставал без модели."""
    tar_bytes = _make_models_tar({"model.cbm": b"MODEL"})

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield tar_bytes

    @contextmanager
    def fake_stream(method, url, **kwargs):
        yield FakeResponse()

    staged = []
    real_tmpdir = db_release.tempfile.TemporaryDirectory

    def recording_tmpdir(*a, **kw):
        staged.append(kw.get("dir"))
        return real_tmpdir(*a, **kw)

    monkeypatch.setenv("KRISHA_MODEL_URL", "https://example.com/models.tar.gz")
    monkeypatch.setattr(db_release.httpx, "stream", fake_stream)
    monkeypatch.setattr(db_release, "_verify_checksum", lambda *a, **kw: None)
    monkeypatch.setattr(db_release.tempfile, "TemporaryDirectory", recording_tmpdir)

    models_dir = tmp_path / "app" / "models"
    assert db_release.download_models(models_dir) is True
    assert staged == [models_dir]
    assert sorted(p.name for p in models_dir.iterdir()) == ["model.cbm"]  # tmp убран


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

    monkeypatch.setenv("KRISHA_MODEL_URL", "https://example.com/models.tar.gz")
    monkeypatch.setattr(db_release.httpx, "stream", fake_stream)
    monkeypatch.setattr(db_release, "_verify_checksum", lambda *a, **kw: None)

    with pytest.raises(ValueError):
        db_release.download_models(tmp_path / "models")


# --- приватный репозиторий данных (REST API + токен) ----------------------


class _FakeJson:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _no_overrides(monkeypatch):
    monkeypatch.delenv("KRISHA_DB_URL", raising=False)
    monkeypatch.delenv("KRISHA_MODEL_URL", raising=False)


def test_private_release_downloads_asset_by_api_with_token(tmp_path, monkeypatch):
    """Ассет ищется по имени в релизе репо данных и качается по /assets/<id>
    с токеном и Accept: octet-stream; checksum — соседним ассетом *.sha256."""
    _no_overrides(monkeypatch)
    monkeypatch.setenv("KRISHA_DB_TOKEN", "tok")
    payload = b"SQLite format 3\x00" + b"y" * 50
    gz_bytes = gzip.compress(payload)
    api = f"https://api.github.com/repos/{db_release.DATA_REPO}"
    release = {"assets": [
        {"name": "krisha.db.gz", "url": f"{api}/releases/assets/1"},
        {"name": "krisha.db.gz.sha256", "url": f"{api}/releases/assets/2"},
        {"name": "krisha_rent.db.gz", "url": f"{api}/releases/assets/3"},
    ]}
    seen = {}

    def fake_get(url, headers=None, **kw):
        if url == f"{api}/releases/tags/db-latest":
            seen["release_auth"] = headers.get("Authorization")
            return _FakeJson(200, release)
        raise AssertionError(f"неожиданный GET {url}")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield gz_bytes

    @contextmanager
    def fake_stream(method, url, headers=None, **kwargs):
        seen["asset"] = (url, headers.get("Authorization"), headers.get("Accept"))
        yield FakeResponse()

    checks = []
    monkeypatch.setattr(db_release.httpx, "get", fake_get)
    monkeypatch.setattr(db_release.httpx, "stream", fake_stream)
    monkeypatch.setattr(
        db_release, "_verify_checksum", lambda path, sha_url, headers=None: checks.append((sha_url, headers))
    )
    db = tmp_path / "krisha.db"
    assert db_release.download(db) is True
    assert db.read_bytes() == payload
    assert seen["release_auth"] == "Bearer tok"
    assert seen["asset"] == (f"{api}/releases/assets/1", "Bearer tok", "application/octet-stream")
    assert [c[0] for c in checks] == [f"{api}/releases/assets/2"]
    assert checks[0][1]["Authorization"] == "Bearer tok"


def test_private_release_without_token_names_the_env(monkeypatch):
    """Приватный релиз без токена отвечает 404 — ошибка обязана подсказать,
    какой переменной не хватает, а не выглядеть как «релиза нет»."""
    _no_overrides(monkeypatch)
    monkeypatch.delenv("KRISHA_DB_TOKEN", raising=False)
    monkeypatch.setattr(db_release.httpx, "get", lambda *a, **kw: _FakeJson(404))
    with pytest.raises(FileNotFoundError, match="KRISHA_DB_TOKEN"):
        db_release._asset_source("db-latest", "krisha.db.gz", "KRISHA_DB_URL")


def test_private_release_missing_asset(monkeypatch):
    _no_overrides(monkeypatch)
    monkeypatch.setenv("KRISHA_DB_TOKEN", "tok")
    monkeypatch.setattr(
        db_release.httpx, "get", lambda *a, **kw: _FakeJson(200, {"assets": []})
    )
    with pytest.raises(FileNotFoundError, match="models.tar.gz"):
        db_release._asset_source("model-latest", "models.tar.gz", "KRISHA_MODEL_URL")


def test_ensure_db_without_token_does_not_crash(tmp_path, monkeypatch, auto_on):
    """Space без секрета стартует (fail-soft), просто без базы."""
    _no_overrides(monkeypatch)
    monkeypatch.delenv("KRISHA_DB_TOKEN", raising=False)
    monkeypatch.setattr(db_release, "DB_PATH", tmp_path / "krisha.db")
    monkeypatch.setattr(db_release.httpx, "get", lambda *a, **kw: _FakeJson(404))
    assert db_release.ensure_db() is False
