"""Хранение базы и моделей в GitHub Release вместо git.

База растёт с каждым рескрейпом, и держать её в git нельзя: GitHub не
принимает файлы больше 100 МБ, а каждый ежедневный коммит записывает в
историю полную копию файла и раздувает репозиторий. Вместо этого
рескрейп-workflow загружает свежую базу как asset релиза ``db-latest``,
а приложение (HF Space, Railway, локальная разработка) скачивает её при
старте, если локального файла нет.

По тому же принципу (аудит, находка #6) модельные артефакты публикуются
в мутабельный релиз ``model-latest`` (asset ``models.tar.gz``) вместо
еженедельного коммита ~15.5 МБ в main. Пока `models/` продолжают
коммититься в main (переходный период — см. issue #74), приложение
предпочитает уже лежащий локально `models/model.cbm` и скачивает из
`model-latest` только если его нет.

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
    MODEL_HI_PATH,
    MODEL_LO_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    MODELS_DIR,
    SPATIAL_REF_PATH,
)

logger = logging.getLogger(__name__)

GITHUB_REPO = os.environ.get("GITHUB_REPO", "Dex719/krisha-fair-price")
RELEASE_TAG = "db-latest"
ASSET_NAME = "krisha.db.gz"

MODEL_RELEASE_TAG = "model-latest"
MODEL_ASSET_NAME = "models.tar.gz"
# Пути (относительно MODELS_DIR) внутри models.tar.gz — то, что реально
# нужно рантайму для оценки; osm_pois.json/osm_zones.json/stats.json туда
# не входят (не нужны для инференса, есть локально из репо/рескрейпа).
MODEL_ARTIFACT_PATHS = [
    MODEL_PATH,
    MODEL_LO_PATH,
    MODEL_HI_PATH,
    MODEL_META_PATH,
    SPATIAL_REF_PATH,
    COMPLEXES_SNAPSHOT_PATH,
]


def db_url() -> str:
    """URL сжатой базы. Переопределяется через env ``KRISHA_DB_URL``."""
    return os.environ.get(
        "KRISHA_DB_URL",
        f"https://github.com/{GITHUB_REPO}/releases/download/{RELEASE_TAG}/{ASSET_NAME}",
    )


def model_url() -> str:
    """URL архива моделей. Переопределяется через env ``KRISHA_MODEL_URL``."""
    return os.environ.get(
        "KRISHA_MODEL_URL",
        f"https://github.com/{GITHUB_REPO}/releases/download/{MODEL_RELEASE_TAG}/{MODEL_ASSET_NAME}",
    )


def _verify_checksum(gz_path: Path, url: str) -> None:
    """Сверяет sha256 архива с файлом `<asset>.sha256` из релиза (если он есть).

    Рескрейп-workflow публикует контрольную сумму рядом с базой. Нет файла
    (старый релиз, кастомный KRISHA_DB_URL) — пропускаем молча: проверка
    появляется бесплатно, ничего не ломая. Не сошлось — ValueError.
    """
    try:
        resp = httpx.get(f"{url}.sha256", follow_redirects=True, timeout=30.0)
    except httpx.HTTPError:
        return
    if resp.status_code != 200:
        return
    expected = resp.text.split()[0].strip().lower() if resp.text.strip() else ""
    if len(expected) != 64:
        return
    digest = hashlib.sha256()
    with open(gz_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(f"Checksum базы не сошёлся: ожидали {expected}, получили {actual}")
    logger.info("Checksum базы сошёлся (sha256 %s…)", expected[:12])


def download(db_path: Path | str = DB_PATH) -> bool:
    """Скачивает, проверяет checksum и распаковывает базу атомарно (tmp → rename)."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = db_url()
    logger.info("Скачиваю базу: %s", url)
    with tempfile.TemporaryDirectory(dir=db_path.parent) as tmpdir:
        gz_path = Path(tmpdir) / ASSET_NAME
        with httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as resp:
            resp.raise_for_status()
            with open(gz_path, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        _verify_checksum(gz_path, url)
        tmp_db = Path(tmpdir) / "krisha.db"
        with gzip.open(gz_path, "rb") as src, open(tmp_db, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp_db, db_path)
    logger.info("База скачана: %s (%.1f МБ)", db_path, db_path.stat().st_size / 1e6)
    return True


def ensure_db(force: bool = False) -> bool:
    """Скачивает базу из релиза, если локального файла нет (или ``force``).

    Возвращает True, если база была скачана. Ошибки сети не роняют
    приложение: без базы работает оценка (модель лежит в репозитории),
    но /stats и алерты будут пустыми до следующего успешного скачивания.
    """
    if os.environ.get("KRISHA_DB_AUTO", "1") == "0" and not force:
        return False
    db_path = Path(DB_PATH)
    if db_path.exists() and db_path.stat().st_size > 0 and not force:
        return False
    try:
        return download(db_path)
    except Exception:
        logger.exception("Не удалось скачать базу из релиза %s", RELEASE_TAG)
        return False


def download_models(models_dir: Path | str = MODELS_DIR) -> bool:
    """Скачивает, проверяет checksum и распаковывает архив моделей атомарно.

    Каждый файл архива распаковывается во временный каталог рядом с
    ``models_dir`` и переносится на место через ``os.replace`` — частично
    распакованный архив никогда не виден рантайму.
    """
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    url = model_url()
    logger.info("Скачиваю модели: %s", url)
    with tempfile.TemporaryDirectory(dir=models_dir.parent) as tmpdir:
        tar_path = Path(tmpdir) / MODEL_ASSET_NAME
        with httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as resp:
            resp.raise_for_status()
            with open(tar_path, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        _verify_checksum(tar_path, url)
        extract_dir = Path(tmpdir) / "extracted"
        extract_dir.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ValueError(f"Подозрительная запись в models.tar.gz: {member.name}")
            tar.extractall(extract_dir)  # noqa: S202 — члены уже провалидированы выше
        for name in os.listdir(extract_dir):
            os.replace(extract_dir / name, models_dir / name)
    logger.info("Модели скачаны в %s", models_dir)
    return True


def ensure_models(force: bool = False) -> bool:
    """Скачивает модели из релиза ``model-latest``, если их нет локально.

    Возвращает True, если модели были скачаны. Если ``models/model.cbm``
    уже лежит локально (текущий снапшот в репо), скачивание пропускается —
    ничего не меняется в поведении до отключения коммита моделей в main.
    Ошибки сети/релиза не роняют приложение: fail-soft, как и для базы.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Скачать базу/модели из GitHub Release")
    parser.add_argument("--force", action="store_true", help="скачать, даже если файл уже есть")
    parser.add_argument(
        "--require", action="store_true", help="выйти с ошибкой, если ничего нет и скачать не удалось"
    )
    parser.add_argument(
        "--models", action="store_true", help="скачать модели (model-latest) вместо базы"
    )
    args = parser.parse_args(argv)

    if args.models:
        downloaded = ensure_models(force=args.force)
        present = Path(MODEL_PATH).exists() and Path(MODEL_PATH).stat().st_size > 0
        if args.require and not present:
            print("Модели отсутствуют и скачать их не удалось", file=sys.stderr)
            return 1
        print("модели скачаны" if downloaded else ("модели уже на месте" if present else "моделей нет"))
        return 0

    downloaded = ensure_db(force=args.force)
    db_path = Path(DB_PATH)
    present = db_path.exists() and db_path.stat().st_size > 0
    if args.require and not present:
        print("База отсутствует и скачать её не удалось", file=sys.stderr)
        return 1
    print("база скачана" if downloaded else ("база уже на месте" if present else "базы нет"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
