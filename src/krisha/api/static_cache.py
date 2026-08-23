"""Статика, сжатая один раз при старте и живущая в памяти процесса.

Зачем. Главная страница — это 78 КБ html, одинаковых для всех. Раньше на
КАЖДЫЙ запрос происходило три вещи: stat + чтение файла с диска
(``FileResponse``), затем ``GZipMiddleware`` заново жал эти 78 КБ уровнем 9
(дефолт starlette), и всё это — в потоке, который в этот момент не обслуживал
остальные запросы. Замер на двух воркерах, 25 клиентов, GET ``/``:

* ``Accept-Encoding: gzip`` — 361 rps, p50 63 мс;
* ``Accept-Encoding: identity`` — 706 rps, p50 33 мс.

То есть половина CPU главной уходила на повторное сжатие неизменного файла.
Здесь файл читается и жмётся ОДИН раз при старте, а запрос — это отдача
готовых байтов из памяти. Разгружается не только сама страница: освободившийся
CPU достаётся ``/api/predict`` и ``/api/health`` (по нему HF решает, жив ли
контейнер).

Почему при старте, а не на этапе сборки образа. Пять файлов gzip-9 — это
десятки миллисекунд на фоне секунд загрузки CatBoost, зато один путь кода для
прод-образа, dev-запуска и тестов. Детерминизм даёт ``mtime=0``: у обоих
воркеров и между рестартами байты одинаковые, значит одинаковые и ETag.

ETag считается от СОДЕРЖИМОГО, а не от ``size+mtime`` как в ``FileResponse``:
mtime в образе — это время COPY при сборке, поэтому раньше каждый деплой
инвалидировал кэш браузера, даже если html не менялся.

Файлы в образе неизменяемы до рестарта контейнера, поэтому перепроверки mtime
на запросе нет: в dev ``uvicorn --reload`` перезапускает процесс сам.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Что имеет смысл жать: текст. webp/woff2/png уже сжаты, gzip поверх них
# тратит CPU и увеличивает размер.
COMPRESSIBLE_SUFFIXES = (
    ".html",
    ".css",
    ".js",
    ".mjs",
    ".json",
    ".svg",
    ".txt",
    ".xml",
    ".map",
    ".webmanifest",
)
# Ниже этого порога сжатие бессмысленно (тот же minimum_size, что был у
# GZipMiddleware): накладные расходы gzip-заголовка съедают выигрыш.
MIN_GZIP_BYTES = 600
GZIP_LEVEL = 9
TEXT_CHARSET_PREFIXES = ("text/", "application/javascript", "application/json", "image/svg+xml")


@dataclass(frozen=True)
class Asset:
    """Один файл: исходные байты, gzip-вариант и ETag на каждый вариант."""

    raw: bytes
    gz: bytes | None
    etag: str
    etag_gz: str | None
    media_type: str

    @property
    def sizes(self) -> tuple[int, int | None]:
        return len(self.raw), (len(self.gz) if self.gz is not None else None)


def media_type_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    media_type = guessed or "application/octet-stream"
    if media_type.startswith(TEXT_CHARSET_PREFIXES) and "charset" not in media_type:
        media_type = f"{media_type}; charset=utf-8"
    return media_type


def _etag(data: bytes) -> str:
    return '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"'


def build_asset(path: Path) -> Asset:
    """Читает файл и готовит оба варианта представления.

    ETag у сжатого и несжатого вариантов РАЗНЫЕ (суффикс ``-gz``): сильный
    ETag идентифицирует конкретное представление ресурса, а Content-Encoding —
    его часть. Один ETag на оба варианта ломает промежуточные кэши: клиенту
    без gzip может прилететь 304 на представление, которого у него нет.
    """
    raw = path.read_bytes()
    etag = _etag(raw)
    gz: bytes | None = None
    etag_gz: str | None = None
    if path.suffix.lower() in COMPRESSIBLE_SUFFIXES and len(raw) >= MIN_GZIP_BYTES:
        # mtime=0 — детерминированные байты (иначе в gzip-заголовок попадает
        # время сжатия и ETag расходятся между воркерами и рестартами).
        gz = gzip.compress(raw, GZIP_LEVEL, mtime=0)
        if len(gz) < len(raw):
            etag_gz = etag[:-1] + '-gz"'
        else:  # редкость, но пусть будет: сжатие не помогло — не отдаём его
            gz = None
    return Asset(raw=raw, gz=gz, etag=etag, etag_gz=etag_gz, media_type=media_type_for(path))


def build_cache(root: Path, names: list[str]) -> dict[str, Asset]:
    """{имя относительно root: Asset} для существующих файлов из names."""
    cache: dict[str, Asset] = {}
    for name in names:
        path = root / name
        try:
            if path.is_file():
                cache[name] = build_asset(path)
        except OSError:  # noqa: PERF203 — файл мог исчезнуть, это не повод падать
            logger.warning("static: не удалось предсжать %s", name, exc_info=True)
    if cache:
        total_raw = sum(a.sizes[0] for a in cache.values())
        total_gz = sum(a.sizes[1] or a.sizes[0] for a in cache.values())
        logger.info(
            "static: предсжато %d файлов, %d КБ → %d КБ",
            len(cache),
            total_raw // 1024,
            total_gz // 1024,
        )
    return cache


def accepts_gzip(accept_encoding: str | None) -> bool:
    return "gzip" in (accept_encoding or "").lower()


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110: список ETag через запятую, ``*`` совпадает с чем угодно.

    Слабый префикс ``W/`` игнорируем при сравнении — для нас это тот же ресурс.
    """
    if not if_none_match:
        return False
    candidates = [c.strip() for c in if_none_match.split(",") if c.strip()]
    if "*" in candidates:
        return True
    target = etag.removeprefix("W/")
    return any(c.removeprefix("W/") == target for c in candidates)


def negotiate(
    asset: Asset,
    *,
    accept_encoding: str | None,
    if_none_match: str | None,
    cache_control: str,
) -> tuple[int, bytes, dict[str, str]]:
    """(статус, тело, заголовки) для одного файла.

    Vary: Accept-Encoding ставится и на 200, и на 304 — иначе промежуточный
    кэш отдаст сжатый ответ клиенту, который gzip не просил.
    """
    use_gzip = asset.gz is not None and accepts_gzip(accept_encoding)
    body = asset.gz if use_gzip else asset.raw
    etag = (asset.etag_gz if use_gzip else asset.etag) or asset.etag
    headers = {
        "ETag": etag,
        "Vary": "Accept-Encoding",
        "Cache-Control": cache_control,
    }
    if use_gzip:
        headers["Content-Encoding"] = "gzip"
    if etag_matches(if_none_match, etag):
        return 304, b"", headers
    headers["Content-Type"] = asset.media_type
    # Content-Length ставим явно: тело фиксированное, а клиенту полезно знать
    # размер заранее (прогресс-бар, HEAD-запросы).
    headers["Content-Length"] = str(len(body))
    return 200, body, headers
