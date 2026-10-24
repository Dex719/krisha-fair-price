"""Тесты scripts/release_upload.py — заливка без окна «файла нет» (аудит 24.10).

gh подменяется фейком с состоянием релизов «на GitHub»; после каждой
мутации проверяем инвариант: у каждого файла в релизе есть либо готовый
ассет, либо сверенный временный.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import release_upload as ru  # noqa: E402

import krisha.db_release as db_release  # noqa: E402

REPO = "Dex719/krisha-db"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeGh:
    def __init__(self, releases=None, corrupt_upload=False, fail_rename_once=False, digest=True):
        # {tag: {name: {"id", "name", "state", "size", "data"}}}
        self.releases = releases or {}
        self.corrupt_upload = corrupt_upload
        self.fail_rename_once = fail_rename_once
        self.digest = digest
        self.next_id = 100
        self.history: list[dict[str, set[str]]] = []
        self.calls: list[list[str]] = []

    def add(self, tag, name, data, state="uploaded"):
        self.next_id += 1
        self.releases.setdefault(tag, {})[name] = {
            "id": self.next_id, "name": name, "state": state, "size": len(data), "data": data,
        }

    def _json(self, tag):
        assets = []
        for a in self.releases[tag].values():
            item = {k: v for k, v in a.items() if k != "data"}
            if self.digest:
                item["digest"] = f"sha256:{_sha(a['data'])}"
            assets.append(item)
        return json.dumps({"tag_name": tag, "assets": assets})

    def _by_id(self, asset_id):
        for tag, assets in self.releases.items():
            for name, a in assets.items():
                if a["id"] == asset_id:
                    return tag, name, a
        raise AssertionError(f"нет ассета {asset_id}")

    def _done(self, out="", code=0, err=""):
        return subprocess.CompletedProcess([], code, out, err)

    def __call__(self, args, stdout=None):
        self.calls.append(args)
        if args[0] == "api" and args[1].startswith(f"repos/{REPO}/releases/tags/"):
            tag = args[1].rsplit("/", 1)[-1]
            if tag not in self.releases:
                return self._done(code=1, err="gh: Not Found (HTTP 404)")
            return self._done(self._json(tag))
        if args[:2] == ["release", "create"]:
            self.releases[args[2]] = {}
            return self._done()
        if args[:2] == ["release", "upload"]:
            tag = args[2]
            for p in args[3:args.index("--repo")]:
                data = Path(p).read_bytes()
                if self.corrupt_upload:
                    data = b"X" + data[1:]  # тот же размер, другое содержимое
                self.add(tag, Path(p).name, data)
            self._snapshot()
            return self._done()
        if args[:3] == ["api", "-X", "DELETE"]:
            tag, name, _ = self._by_id(int(args[3].rsplit("/", 1)[-1]))
            del self.releases[tag][name]
            self._snapshot()
            return self._done()
        if args[:3] == ["api", "-X", "PATCH"]:
            if self.fail_rename_once:
                self.fail_rename_once = False
                return self._done(code=1, err="HTTP 502")
            tag, name, a = self._by_id(int(args[3].rsplit("/", 1)[-1]))
            new = args[args.index("-f") + 1].removeprefix("name=")
            assert new not in self.releases[tag], "GitHub не даст переименовать в занятое имя"
            del self.releases[tag][name]
            a["name"] = new
            self.releases[tag][new] = a
            self._snapshot()
            return self._done()
        if args[0] == "api" and args[1] == "-H":
            _, _, a = self._by_id(int(args[3].rsplit("/", 1)[-1]))
            stdout.write(a["data"])
            return subprocess.CompletedProcess([], 0, None, b"")
        raise AssertionError(f"неожиданный вызов gh: {args}")

    def _snapshot(self):
        self.history.append({t: set(a) for t, a in self.releases.items()})


def _files(tmp_path, db=b"new-db", sha=None):
    f1 = tmp_path / "krisha.db.gz"
    f1.write_bytes(db)
    f2 = tmp_path / "krisha.db.gz.sha256"
    f2.write_text(sha or _sha(db))
    return [f1, f2]


def _available(names: set[str], base: str) -> bool:
    return base in names or f"{base}{ru.TMP_SUFFIX}" in names


@pytest.fixture()
def gh(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(ru, "run_gh", fake)
    return fake


def test_suffix_matches_reader():
    assert ru.TMP_SUFFIX == db_release.UPLOAD_TMP_SUFFIX


@pytest.mark.parametrize("digest", [True, False])
def test_replaces_assets_without_gap(tmp_path, monkeypatch, digest):
    gh = FakeGh(digest=digest)
    monkeypatch.setattr(ru, "run_gh", gh)
    gh.add("db-latest", "krisha.db.gz", b"old-db")
    gh.add("db-latest", "krisha.db.gz.sha256", _sha(b"old-db").encode())
    gh.add("db-latest", "krisha_rent.db.gz", b"rent")

    ru.upload(REPO, "db-latest", _files(tmp_path), sleep=lambda s: None)

    rel = gh.releases["db-latest"]
    assert set(rel) == {"krisha.db.gz", "krisha.db.gz.sha256", "krisha_rent.db.gz"}
    assert rel["krisha.db.gz"]["data"] == b"new-db"
    assert rel["krisha.db.gz.sha256"]["data"].decode() == _sha(b"new-db")
    assert rel["krisha_rent.db.gz"]["data"] == b"rent"  # чужие ассеты не тронуты
    # ни в один момент база или её sha не пропадали целиком
    for snap in gh.history:
        assert _available(snap["db-latest"], "krisha.db.gz")
        assert _available(snap["db-latest"], "krisha.db.gz.sha256")
    # без digest проверка шла скачиванием
    downloaded = any(c[:2] == ["api", "-H"] for c in gh.calls)
    assert downloaded is (not digest)


def test_bad_upload_keeps_old_assets(tmp_path, monkeypatch):
    gh = FakeGh(corrupt_upload=True)
    monkeypatch.setattr(ru, "run_gh", gh)
    gh.add("db-latest", "krisha.db.gz", b"old-db")
    sleeps = []

    with pytest.raises(ru.UploadError, match="sha256"):
        ru.upload(REPO, "db-latest", _files(tmp_path)[:1], sleep=sleeps.append)

    assert sleeps == list(ru.RETRY_WAITS)
    assert set(gh.releases["db-latest"]) == {"krisha.db.gz"}
    assert gh.releases["db-latest"]["krisha.db.gz"]["data"] == b"old-db"


def test_interrupted_swap_is_readable_and_recovered(tmp_path, monkeypatch):
    """Сбой между удалением старого и переименованием: в релизе только
    .uploading — читатель берёт его, а следующий запуск возвращает имя."""
    gh = FakeGh(fail_rename_once=True)
    monkeypatch.setattr(ru, "run_gh", gh)
    gh.add("db-latest", "krisha_rent.db.gz", b"old")

    # первая попытка падает на PATCH, повтор доводит дело
    ru.upload(REPO, "db-latest", [_files(tmp_path)[0]], sleep=lambda s: None)
    assert set(gh.releases["db-latest"]) == {"krisha_rent.db.gz", "krisha.db.gz"}

    # отдельно: оборванное состояние «только временный» читается db_release
    gh2 = FakeGh()
    gh2.add("db-latest", "krisha_rent.db.gz.uploading", b"new")
    release_json = json.loads(gh2._json("db-latest"))
    for a in release_json["assets"]:
        a["url"] = f"https://api/assets/{a['id']}"

    class Resp:
        status_code = 200

        def json(self):
            return release_json

        def raise_for_status(self):
            pass

    monkeypatch.delenv("KRISHA_RENT_DB_URL", raising=False)
    monkeypatch.setattr(db_release.httpx, "get", lambda *a, **kw: Resp())
    url, *_ = db_release._asset_source("db-latest", "krisha_rent.db.gz", "KRISHA_RENT_DB_URL")
    assert url.endswith(str(gh2.releases["db-latest"]["krisha_rent.db.gz.uploading"]["id"]))

    # и следующий запуск сначала возвращает временному имя, потом льёт новое
    monkeypatch.setattr(ru, "run_gh", gh2)
    rent = tmp_path / "krisha_rent.db.gz"
    rent.write_bytes(b"newer")
    ru.upload(REPO, "db-latest", [rent], sleep=lambda s: None)
    assert gh2.releases["db-latest"]["krisha_rent.db.gz"]["data"] == b"newer"
    assert all(_available(s["db-latest"], "krisha_rent.db.gz") for s in gh2.history)


def test_stale_unfinished_tmp_is_removed(tmp_path, gh):
    gh.add("db-latest", "krisha.db.gz", b"old-db")
    gh.add("db-latest", "krisha.db.gz.uploading", b"half", state="starter")
    ru.upload(REPO, "db-latest", _files(tmp_path)[:1], sleep=lambda s: None)
    assert set(gh.releases["db-latest"]) == {"krisha.db.gz"}
    assert gh.releases["db-latest"]["krisha.db.gz"]["data"] == b"new-db"


def test_missing_release_created_only_with_title(tmp_path, gh):
    with pytest.raises(ru.UploadError, match="--create-title"):
        ru.upload(REPO, "model-latest", _files(tmp_path)[:1], sleep=lambda s: None)
    ru.upload(REPO, "model-latest", _files(tmp_path)[:1], title="model-latest",
              notes="веса", sleep=lambda s: None)
    assert set(gh.releases["model-latest"]) == {"krisha.db.gz"}


def test_gh_failure_is_not_treated_as_missing_release(tmp_path, monkeypatch):
    def broken(args, stdout=None):
        return subprocess.CompletedProcess(args, 1, "", "HTTP 502: Bad Gateway")

    monkeypatch.setattr(ru, "run_gh", broken)
    with pytest.raises(ru.UploadError, match="502"):
        ru.upload(REPO, "model-latest", _files(tmp_path)[:1], title="t", sleep=lambda s: None)


def test_cli_returns_1_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ru, "upload", lambda *a, **k: (_ for _ in ()).throw(ru.UploadError("x")))
    assert ru.main(["--tag", "db-latest", str(tmp_path / "a")]) == 1
