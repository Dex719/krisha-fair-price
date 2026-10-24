#!/usr/bin/env python
"""Надёжная заливка ассетов в релиз репозитория данных (аудит 24.10).

Зачем. ``gh release upload --clobber`` сначала УДАЛЯЕТ ассет, потом льёт
новый. Сбой посередине (сеть, 5xx, отмена джобы) оставлял ``db-latest`` /
``model-latest`` без файла: аренда принимала это за «первый запуск» и
затирала историю пустой базой, а прод после рестарта неделю жил без модели.

Как теперь (для каждого файла ``<имя>``):

1. если ``<имя>`` нет, а ``<имя>.uploading`` есть — прошлый запуск умер между
   удалением старого и переименованием нового; временный уже сверен,
   возвращаем ему настоящее имя (иначе ниже он был бы удалён как мусор);
2. недолитые ``<имя>.uploading`` прошлых запусков удаляются;
3. файл льётся под именем ``<имя>.uploading``;
4. sha256 залитого сверяется с локальным (по ``digest`` из API, а если его
   нет — скачиванием); не сошлось — временный удаляется, старый не тронут;
5. только после проверки ВСЕХ файлов: старый ``<имя>`` удаляется, временный
   переименовывается (PATCH имени ассета).

Окно «старого уже нет, новый ещё .uploading» — один API-вызов; чтение
(krisha.db_release._asset_source) в это окно берёт временный файл. Порядок
файлов важен: база раньше своего .sha256 (читатель сверяет с обоими).

Релиза нет — создаётся, если заданы ``--create-title``/``--create-notes``
(model-latest и дневные model-YYYY-MM-DD): прежнее ``upload || release
create`` на полуудалённом ассете оставляло model-latest без архива.

Запуск (нужны ``gh`` в PATH и ``GH_TOKEN``):
    python scripts/release_upload.py --repo Dex719/krisha-db --tag db-latest \\
        /tmp/krisha.db.gz /tmp/krisha.db.gz.sha256
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("release_upload")

# Должен совпадать с krisha.db_release.UPLOAD_TMP_SUFFIX (проверяет тест):
# скрипт не импортирует пакет, чтобы работать и без `pip install -e .`.
TMP_SUFFIX = ".uploading"
RETRY_WAITS = (10, 30)


class UploadError(RuntimeError):
    pass


def run_gh(args: list[str], *, stdout=None) -> subprocess.CompletedProcess:
    """Тонкая обёртка над gh (подменяется в тестах)."""
    return subprocess.run(
        ["gh", *args],
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=stdout is None,
        check=False,
    )


def _err(proc: subprocess.CompletedProcess) -> str:
    err = proc.stderr
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    return (err or "").strip()


def _ok(proc: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
    if proc.returncode != 0:
        raise UploadError(f"{what}: {_err(proc)[:500]}")
    return proc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_release(repo: str, tag: str) -> dict | None:
    """JSON релиза или None, если его нет. Любая другая ошибка — исключение:
    «gh упал» нельзя путать с «релиза нет»."""
    proc = run_gh(["api", f"repos/{repo}/releases/tags/{tag}"])
    if proc.returncode != 0:
        if "404" in _err(proc) or "Not Found" in _err(proc):
            return None
        raise UploadError(f"чтение релиза {tag}: {_err(proc)[:500]}")
    return json.loads(proc.stdout)


def ensure_release(repo: str, tag: str, title: str | None, notes: str | None) -> dict:
    rel = get_release(repo, tag)
    if rel is not None:
        return rel
    if title is None:
        raise UploadError(f"релиза {repo}@{tag} нет, а --create-title не задан")
    logger.info("Релиза %s нет — создаю", tag)
    _ok(
        run_gh(["release", "create", tag, "--repo", repo, "--title", title,
                "--notes", notes or title]),
        f"создание релиза {tag}",
    )
    rel = get_release(repo, tag)
    if rel is None:
        raise UploadError(f"релиз {tag} создан, но не читается")
    return rel


def _assets(rel: dict) -> dict[str, dict]:
    return {a["name"]: a for a in rel.get("assets", [])}


def delete_asset(repo: str, asset: dict) -> None:
    _ok(
        run_gh(["api", "-X", "DELETE", f"repos/{repo}/releases/assets/{asset['id']}"]),
        f"удаление ассета {asset['name']}",
    )


def rename_asset(repo: str, asset: dict, name: str) -> None:
    _ok(
        run_gh(["api", "-X", "PATCH", f"repos/{repo}/releases/assets/{asset['id']}",
                "-f", f"name={name}"]),
        f"переименование {asset['name']} → {name}",
    )


def remote_sha256(repo: str, asset: dict, workdir: Path) -> str:
    """sha256 залитого ассета: из ``digest`` API, иначе — скачав его."""
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest.removeprefix("sha256:").lower()
    dest = workdir / f"verify-{asset['id']}"
    with open(dest, "wb") as fh:
        _ok(
            run_gh(["api", "-H", "Accept: application/octet-stream",
                    f"repos/{repo}/releases/assets/{asset['id']}"], stdout=fh),
            f"скачивание {asset['name']} для проверки",
        )
    try:
        return sha256_file(dest)
    finally:
        dest.unlink(missing_ok=True)


def _upload_once(
    repo: str, tag: str, files: list[Path], title: str | None, notes: str | None
) -> None:
    rel = ensure_release(repo, tag, title, notes)
    names = [f.name for f in files]
    assets = _assets(rel)

    # 1–2. Хвосты прошлых запусков.
    for name in names:
        tmp = assets.get(f"{name}{TMP_SUFFIX}")
        if tmp is None:
            continue
        if name not in assets and tmp.get("state") == "uploaded":
            logger.warning("%s нет, но есть %s%s — возвращаю имя (прошлый запуск оборвался)",
                           name, name, TMP_SUFFIX)
            rename_asset(repo, tmp, name)
        else:
            logger.info("Удаляю хвост прошлого запуска %s%s", name, TMP_SUFFIX)
            delete_asset(repo, tmp)

    with tempfile.TemporaryDirectory(prefix="release-upload-") as td:
        workdir = Path(td)
        local_sha: dict[str, str] = {}
        sizes: dict[str, int] = {}
        # 3. Заливка под временными именами.
        staged = []
        for f in files:
            tmp_path = workdir / f"{f.name}{TMP_SUFFIX}"
            shutil.copyfile(f, tmp_path)
            local_sha[f.name] = sha256_file(tmp_path)
            sizes[f.name] = tmp_path.stat().st_size
            staged.append(str(tmp_path))
        _ok(run_gh(["release", "upload", tag, *staged, "--repo", repo]), "заливка")

        # 4. Проверка до того, как трогать старые файлы.
        assets = _assets(_require_release(repo, tag))
        for name in names:
            tmp = assets.get(f"{name}{TMP_SUFFIX}")
            problem = None
            if tmp is None or tmp.get("state", "uploaded") != "uploaded":
                problem = "временный ассет не появился"
            elif tmp.get("size") is not None and tmp["size"] != sizes[name]:
                problem = f"размер {tmp['size']} ≠ {sizes[name]}"
            elif remote_sha256(repo, tmp, workdir) != local_sha[name]:
                problem = "sha256 не сошёлся"
            if problem:
                for n in names:
                    t = assets.get(f"{n}{TMP_SUFFIX}")
                    if t is not None:
                        delete_asset(repo, t)
                raise UploadError(f"проверка {name}{TMP_SUFFIX}: {problem}; старые файлы не тронуты")
            logger.info("%s%s залит и сверен (sha256 %s…)", name, TMP_SUFFIX, local_sha[name][:12])

    # 5. Подмена: удалить старый → переименовать новый, по файлу за раз.
    for name in names:
        assets = _assets(_require_release(repo, tag))
        old = assets.get(name)
        tmp = assets[f"{name}{TMP_SUFFIX}"]
        if old is not None:
            delete_asset(repo, old)
        rename_asset(repo, tmp, name)

    assets = _assets(_require_release(repo, tag))
    missing = [n for n in names if n not in assets]
    if missing:
        raise UploadError(f"после подмены нет ассетов: {missing}")
    logger.info("Релиз %s@%s: обновлены %s", repo, tag, ", ".join(names))


def _require_release(repo: str, tag: str) -> dict:
    rel = get_release(repo, tag)
    if rel is None:
        raise UploadError(f"релиз {repo}@{tag} пропал посреди заливки")
    return rel


def upload(
    repo: str,
    tag: str,
    files: list[Path],
    title: str | None = None,
    notes: str | None = None,
    sleep=time.sleep,
) -> None:
    for f in files:
        if not f.is_file():
            raise UploadError(f"нет локального файла {f}")
    if len({f.name for f in files}) != len(files):
        raise UploadError("имена файлов должны быть уникальны")
    for attempt, wait in enumerate((*RETRY_WAITS, None), start=1):
        try:
            _upload_once(repo, tag, files, title, notes)
            return
        except (UploadError, json.JSONDecodeError, KeyError) as exc:
            if wait is None:
                raise
            logger.warning("Заливка в %s: попытка %s не удалась (%s) — повтор через %s с",
                           tag, attempt, exc, wait)
            sleep(wait)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Заливка ассетов в релиз без окна «файла нет»")
    parser.add_argument("--repo", default=os.environ.get("KRISHA_DATA_REPO", "Dex719/krisha-db"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--create-title", help="создать релиз с этим заголовком, если его нет")
    parser.add_argument("--create-notes", help="описание создаваемого релиза")
    parser.add_argument("files", nargs="+", type=Path,
                        help="файлы; имя ассета = имя файла; база раньше своего .sha256")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        upload(args.repo, args.tag, args.files, args.create_title, args.create_notes)
    except UploadError as exc:
        print(f"release_upload: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
