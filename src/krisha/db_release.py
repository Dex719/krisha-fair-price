"""Хранение базы и моделей в релизах приватного репозитория данных.

База растёт с каждым рескрейпом, и держать её в git нельзя: GitHub не
принимает файлы больше 100 МБ, а каждый ежедневный коммит записывает в
историю полную копию файла и раздувает репозиторий. Вместо этого
рескрейп-workflow загружает свежую базу как asset релиза ``db-latest``,
а приложение (HF Space, локальная разработка) скачивает её при старте,
если локального файла нет.

По тому же принципу (аудит, находка #6; issue #74) модельные артефакты
живут в мутабельном релизе ``model-latest`` (asset ``models.tar.gz``), а не
в git: рантайм скачивает их при старте, если `models/model.cbm` нет.

Релизы лежат в ПРИВАТНОМ репозитории данных (``KRISHA_DATA_REPO``,
по умолчанию Dex719/krisha-db): код публичный, а накопленная история
объявлений и веса модели — нет. Приватный ассет по прямой ссылке
``/releases/download/`` не отдаётся, поэтому качаем через REST API с
токеном ``KRISHA_DB_TOKEN`` (fine-grained, доступ только к репо данных).
``KRISHA_DB_URL``/``KRISHA_MODEL_URL`` по-прежнему подменяют источник
прямой ссылкой без токена — для локальной разработки и тестов.

CLI: ``python -m krisha.db_release [--force] [--require]`` — база;
``python -m krisha.db_release --models [--force] [--require]`` — модели.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import logging
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

import httpx

from krisha.config import (
    COMPLEXES_SNAPSHOT_PATH,
    DB_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    MODEL_QUANTILE_PATH,
    MODELS_DIR,
    RENT_DB_PATH,
    RENT_MODEL_META_PATH,
    RENT_MODEL_PATH,
    RENT_MODEL_QUANTILE_PATH,
    RENT_MODELS_DIR,
    RENT_SPATIAL_REF_PATH,
    SPATIAL_REF_PATH,
)

logger = logging.getLogger(__name__)

DATA_REPO = os.environ.get("KRISHA_DATA_REPO", "Dex719/krisha-db")
TOKEN_ENV = "KRISHA_DB_TOKEN"
GITHUB_API = "https://api.github.com"
RELEASE_TAG = "db-latest"
ASSET_NAME = "krisha.db.gz"
RENT_ASSET_NAME = "krisha_rent.db.gz"

# Аудит 24.10: `gh release upload --clobber` сначала удалял ассет, потом лил
# новый — сбой посередине оставлял релиз без файла. Теперь заливка
# (scripts/release_upload.py) кладёт файл под именем `<имя>.uploading`,
# сверяет sha256, удаляет старый и переименовывает новый. В коротком окне
# между удалением и переименованием есть только временный файл — чтение
# ниже его подхватывает, а не считает ассет отсутствующим.
UPLOAD_TMP_SUFFIX = ".uploading"

MODEL_RELEASE_TAG = "model-latest"
MODEL_ASSET_NAME = "models.tar.gz"
# Пути (относительно MODELS_DIR) внутри models.tar.gz — то, что реально
# нужно рантайму для оценки; osm_pois.json/osm_zones.json/stats.json туда
# не входят (не нужны для инференса, есть локально из репо/рескрейпа).
MODEL_ARTIFACT_PATHS = [
    MODEL_PATH,
    MODEL_QUANTILE_PATH,
    MODEL_META_PATH,
    SPATIAL_REF_PATH,
    COMPLEXES_SNAPSHOT_PATH,
]
# Модель аренды — отдельный архив в том же релизе: ретрейны продажи и
# аренды публикуются независимо и не затирают артефакты друг друга.
RENT_MODEL_ASSET_NAME = "models_rent.tar.gz"
RENT_MODEL_ARTIFACT_PATHS = [
    RENT_MODEL_PATH,
    RENT_MODEL_QUANTILE_PATH,
    RENT_MODEL_META_PATH,
    RENT_SPATIAL_REF_PATH,
]


def db_url() -> str:
    """Источник сжатой базы (для логов). Переопределяется через env ``KRISHA_DB_URL``."""
    return os.environ.get(
        "KRISHA_DB_URL",
        f"https://github.com/{DATA_REPO}/releases/download/{RELEASE_TAG}/{ASSET_NAME}",
    )


def model_url() -> str:
    """Источник архива моделей (для логов). Переопределяется через env ``KRISHA_MODEL_URL``."""
    return os.environ.get(
        "KRISHA_MODEL_URL",
        f"https://github.com/{DATA_REPO}/releases/download/{MODEL_RELEASE_TAG}/{MODEL_ASSET_NAME}",
    )


def _api_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get(TOKEN_ENV)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def release_asset_urls(tag: str) -> dict[str, str]:
    """{имя: API-url} готовых (state=uploaded) ассетов релиза ``tag``.

    FileNotFoundError — релиза нет (или нет доступа к приватному репо)."""
    resp = httpx.get(
        f"{GITHUB_API}/repos/{DATA_REPO}/releases/tags/{tag}", headers=_api_headers(), timeout=30.0
    )
    if resp.status_code == 404:
        hint = "" if os.environ.get(TOKEN_ENV) else f" — репозиторий приватный, задайте {TOKEN_ENV}"
        raise FileNotFoundError(f"Релиз {DATA_REPO}@{tag} не найден{hint}")
    resp.raise_for_status()
    # state != uploaded — заливка ещё идёт (или оборвалась): такой ассет не читаем
    return {
        a["name"]: a["url"]
        for a in resp.json().get("assets", [])
        if a.get("state", "uploaded") == "uploaded"
    }


def _asset_source(tag: str, name: str, url_env: str) -> tuple[str, dict, list[str], dict]:
    """Откуда качать ассет: (url, заголовки, url-ы контрольной суммы, их заголовки).

    ``url_env`` задан — прямая ссылка без токена, как раньше; checksum — по
    соседнему ``<url>.sha256``. Иначе ассет ищется по имени в релизе ``tag``
    репозитория данных через API: приватный ассет отдаётся только по
    ``/releases/assets/<id>`` с ``Accept: application/octet-stream``.
    Редирект на подписанную ссылку хранилища httpx проходит сам и
    Authorization на чужой хост не переносит.

    Нет ``name``, но есть ``name.uploading`` — это окно атомарной заливки
    (старый удалён, новый ещё не переименован, см. UPLOAD_TMP_SUFFIX):
    берём временный, он уже сверен по sha256. Кандидатов контрольной суммы
    может быть два (основной и временный .sha256) — файл обязан совпасть
    хотя бы с одним: база и её .sha256 переименовываются по очереди.
    """
    override = os.environ.get(url_env)
    if override:
        return override, {}, [f"{override}.sha256"], {}
    assets = release_asset_urls(tag)
    url = assets.get(name)
    if url is None:
        url = assets.get(f"{name}{UPLOAD_TMP_SUFFIX}")
        if url is None:
            raise FileNotFoundError(f"В релизе {DATA_REPO}@{tag} нет ассета {name}")
        logger.warning(
            "В релизе %s@%s есть только %s%s — идёт заливка, беру временный",
            DATA_REPO, tag, name, UPLOAD_TMP_SUFFIX,
        )
    sha_urls = [
        assets[n]
        for n in (f"{name}.sha256", f"{name}.sha256{UPLOAD_TMP_SUFFIX}")
        if n in assets
    ]
    octet = _api_headers("application/octet-stream")
    return url, octet, sha_urls, octet


def _fetch(url: str, headers: dict, dest: Path) -> None:
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=300.0) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)


class ChecksumUnavailable(ValueError):
    """Строгая проверка: контрольную сумму получить не удалось."""


def _fetch_expected_sha(sha_url: str, headers: dict | None) -> str | None:
    try:
        resp = httpx.get(sha_url, headers=headers or {}, follow_redirects=True, timeout=30.0)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    expected = resp.text.split()[0].strip().lower() if resp.text.strip() else ""
    return expected if len(expected) == 64 else None


def _verify_checksum(
    gz_path: Path,
    sha_url: str | list[str] | None,
    headers: dict | None = None,
    strict: bool = False,
) -> None:
    """Сверяет sha256 архива с файлом `<asset>.sha256` из релиза (если он есть).

    Рескрейп-workflow публикует контрольную сумму рядом с базой. Нет файла
    (старый релиз, кастомный KRISHA_DB_URL) — по умолчанию пропускаем молча:
    проверка появляется бесплатно, ничего не ломая. ``strict`` (учение по
    восстановлению, скачивание базы аренды в CI) — тогда ненайденная или
    нескачавшаяся контрольная сумма — ChecksumUnavailable, а не тишина.
    Не сошлось — ValueError. Кандидатов может быть несколько (окно
    атомарной заливки, см. _asset_source) — достаточно совпасть с одним.
    """
    urls = [sha_url] if isinstance(sha_url, str) else [u for u in (sha_url or []) if u]
    expected = [e for e in (_fetch_expected_sha(u, headers) for u in urls) if e]
    if not expected:
        if strict:
            raise ChecksumUnavailable(
                f"Контрольная сумма для {gz_path.name} не получена "
                f"({'нет ассета .sha256' if not urls else 'скачать не удалось'})"
            )
        return
    digest = hashlib.sha256()
    with open(gz_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual not in expected:
        raise ValueError(
            f"Checksum базы не сошёлся: ожидали {' или '.join(expected)}, получили {actual}"
        )
    logger.info("Checksum базы сошёлся (sha256 %s…)", actual[:12])


def _strict_kw(strict: bool) -> dict:
    # Флаг передаём, только когда он включён: старые вызовы (и подмены в
    # тестах) с прежней сигнатурой продолжают работать.
    return {"strict": True} if strict else {}


def download(
    db_path: Path | str = DB_PATH,
    asset: str = ASSET_NAME,
    url_env: str = "KRISHA_DB_URL",
    strict_checksum: bool = False,
) -> bool:
    """Скачивает, проверяет checksum и распаковывает базу атомарно (tmp → rename).

    ``strict_checksum`` — без контрольной суммы не распаковывать (см. _verify_checksum).
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Скачиваю базу %s из %s@%s", asset, DATA_REPO, RELEASE_TAG)
    url, headers, sha_url, sha_headers = _asset_source(RELEASE_TAG, asset, url_env)
    with tempfile.TemporaryDirectory(dir=db_path.parent) as tmpdir:
        gz_path = Path(tmpdir) / asset
        _fetch(url, headers, gz_path)
        _verify_checksum(gz_path, sha_url, sha_headers, **_strict_kw(strict_checksum))
        tmp_db = Path(tmpdir) / "krisha.db"
        with gzip.open(gz_path, "rb") as src, open(tmp_db, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp_db, db_path)
    logger.info("База скачана: %s (%.1f МБ)", db_path, db_path.stat().st_size / 1e6)
    return True


def ensure_db(force: bool = False, strict_checksum: bool = False) -> bool:
    """Скачивает базу из релиза, если локального файла нет (или ``force``).

    Возвращает True, если база была скачана. Ошибки сети не роняют
    приложение: без базы работает оценка (модель скачивается отдельно, см.
    ensure_models), но /stats и алерты будут пустыми до следующего
    успешного скачивания.
    """
    if os.environ.get("KRISHA_DB_AUTO", "1") == "0" and not force:
        return False
    db_path = Path(DB_PATH)
    if db_path.exists() and db_path.stat().st_size > 0 and not force:
        return False
    try:
        if strict_checksum:
            return download(db_path, strict_checksum=True)
        return download(db_path)
    except Exception:
        logger.exception("Не удалось скачать базу из релиза %s", RELEASE_TAG)
        return False


def ensure_rent_db(force: bool = False, strict_checksum: bool = False) -> bool:
    """То же для базы аренды (ассет krisha_rent.db.gz). Fail-soft."""
    if os.environ.get("KRISHA_DB_AUTO", "1") == "0" and not force:
        return False
    db_path = Path(RENT_DB_PATH)
    if db_path.exists() and db_path.stat().st_size > 0 and not force:
        return False
    try:
        extra = {"strict_checksum": True} if strict_checksum else {}
        return download(db_path, RENT_ASSET_NAME, "KRISHA_RENT_DB_URL", **extra)
    except Exception:
        logger.exception("Не удалось скачать базу аренды из релиза %s", RELEASE_TAG)
        return False


def download_models(
    models_dir: Path | str = MODELS_DIR,
    asset: str = MODEL_ASSET_NAME,
    url_env: str = "KRISHA_MODEL_URL",
    strict_checksum: bool = False,
) -> bool:
    """Скачивает, проверяет checksum и распаковывает архив моделей атомарно.

    Каждый файл архива распаковывается во временный каталог рядом с
    ``models_dir`` и переносится на место через ``os.replace`` — частично
    распакованный архив никогда не виден рантайму.
    """
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Скачиваю модели %s из %s@%s", asset, DATA_REPO, MODEL_RELEASE_TAG)
    url, headers, sha_url, sha_headers = _asset_source(MODEL_RELEASE_TAG, asset, url_env)
    # Временный каталог — ВНУТРИ models_dir, а не рядом: в образе Space
    # родитель (/app) принадлежит root, писать туда пользователь app не может,
    # и скачивание молча падало (fail-soft) — прод вставал без модели.
    # Внутри models_dir os.replace остаётся атомарным (та же ФС).
    with tempfile.TemporaryDirectory(dir=models_dir) as tmpdir:
        tar_path = Path(tmpdir) / asset
        _fetch(url, headers, tar_path)
        _verify_checksum(tar_path, sha_url, sha_headers, **_strict_kw(strict_checksum))
        extract_dir = Path(tmpdir) / "extracted"
        extract_dir.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ValueError(f"Подозрительная запись в {asset}: {member.name}")
            tar.extractall(extract_dir)  # noqa: S202 — члены уже провалидированы выше
        for name in os.listdir(extract_dir):
            os.replace(extract_dir / name, models_dir / name)
    logger.info("Модели скачаны в %s", models_dir)
    return True


def ensure_models(force: bool = False) -> bool:
    """Скачивает модели из релиза ``model-latest``, если их нет локально.

    Возвращает True, если модели были скачаны. Если ``models/model.cbm``
    уже лежит локально (скачан раньше или положен руками), скачивание
    пропускается. Ошибки сети/релиза не роняют приложение: fail-soft, как и
    для базы.
    """
    if os.environ.get("KRISHA_MODEL_AUTO", "1") == "0" and not force:
        return False
    model_path = Path(MODEL_PATH)
    if model_path.exists() and model_path.stat().st_size > 0 and not force:
        return False
    try:
        return download_models(MODELS_DIR)
    except Exception:
        logger.exception("Не удалось скачать модели из релиза %s", MODEL_RELEASE_TAG)
        return False


def ensure_rent_models(force: bool = False) -> bool:
    """То же для модели аренды (ассет models_rent.tar.gz → models/rent/). Fail-soft."""
    if os.environ.get("KRISHA_MODEL_AUTO", "1") == "0" and not force:
        return False
    model_path = Path(RENT_MODEL_PATH)
    if model_path.exists() and model_path.stat().st_size > 0 and not force:
        return False
    try:
        return download_models(RENT_MODELS_DIR, RENT_MODEL_ASSET_NAME, "KRISHA_RENT_MODEL_URL")
    except Exception:
        logger.exception("Не удалось скачать модель аренды из релиза %s", MODEL_RELEASE_TAG)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Скачать базу/модели из GitHub Release")
    parser.add_argument("--force", action="store_true", help="скачать, даже если файл уже есть")
    parser.add_argument(
        "--require", action="store_true", help="выйти с ошибкой, если ничего нет и скачать не удалось"
    )
    parser.add_argument(
        "--models", action="store_true", help="скачать модели (model-latest) вместо базы"
    )
    parser.add_argument(
        "--rent", action="store_true", help="аренда: база krisha_rent.db / модели в models/rent/"
    )
    parser.add_argument(
        "--strict-checksum",
        action="store_true",
        help="база: без опубликованной контрольной суммы не распаковывать (ошибка, а не тишина)",
    )
    args = parser.parse_args(argv)

    if args.models:
        ensure, path = (ensure_rent_models, RENT_MODEL_PATH) if args.rent else (ensure_models, MODEL_PATH)
        downloaded = ensure(force=args.force)
        present = Path(path).exists() and Path(path).stat().st_size > 0
        if args.require and not present:
            print("Модели отсутствуют и скачать их не удалось", file=sys.stderr)
            return 1
        print("модели скачаны" if downloaded else ("модели уже на месте" if present else "моделей нет"))
        return 0

    ensure, db_path = (ensure_rent_db, Path(RENT_DB_PATH)) if args.rent else (ensure_db, Path(DB_PATH))
    if args.strict_checksum:
        downloaded = ensure(force=args.force, strict_checksum=True)
    else:
        downloaded = ensure(force=args.force)
    present = db_path.exists() and db_path.stat().st_size > 0
    if args.require and not present:
        print("База отсутствует и скачать её не удалось", file=sys.stderr)
        return 1
    print("база скачана" if downloaded else ("база уже на месте" if present else "базы нет"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
