#!/usr/bin/env python
"""Публикует датированный снапшот-релиз базы (issue #74, часть 1).

Дополняет мутабельный ``db-latest`` (не трогаем) архивной историей:
после каждого успешного рескрейпа (продажа и/или аренда) создаётся —
или дополняется, если уже создан в этом же проходе суток — релиз с
тегом ``snapshot-YYYY-MM-DD``, содержащий сжатую базу(-ы), их sha256 и
человекочитаемую сводку счётчиков прохода в описании релиза.

Ротация: снапшоты старше ``--rotate-keep-days`` дней удаляются, КРОМЕ
воскресных (они остаются архивом навсегда). Ротация fail-soft — ошибка
не должна ронять рескрейп-workflow.

Запуск из workflow (после шага "Upload DB to release"):
    python scripts/publish_snapshot.py \\
      --db-gz /tmp/krisha.db.gz --sha256 /tmp/krisha.db.gz.sha256 \\
      --asset-name krisha.db.gz --stats-json /tmp/rescrape_stats.json \\
      --label "Продажа" --rotate-keep-days 14

Требует ``gh`` в PATH и env ``GH_TOKEN`` (как остальные шаги workflow).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO = "Dex719/krisha-fair-price"
TAG_PREFIX = "snapshot"


def run_gh(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args, "--repo", REPO], capture_output=True, text=True, check=check
    )


def release_exists(tag: str) -> bool:
    result = run_gh(["release", "view", tag, "--json", "tagName"], check=False)
    return result.returncode == 0


def human_summary(label: str, stats: dict) -> str:
    """Читаемая сводка прохода + сырой JSON в details-блоке."""
    if "delisted" not in stats:
        delisted_line = "- снято с продажи: **?**"
    else:
        delisted = stats.get("delisted")
        delisted_line = (
            "- детект снятий: **n/a**"
            if delisted is None
            else f"- снято с продажи: **{delisted}**"
        )
    lines = [
        f"## {label}",
        "",
        f"- в выдаче: **{stats.get('found_in_search', '?')}**",
        f"- новых объявлений: **{stats.get('discovered_new', stats.get('new_listings', '?'))}**",
        f"- изменений цены: **{stats.get('price_changes', '?')}**",
        delisted_line,
    ]
    failed = stats.get("failed_shards") or []
    if failed:
        lines.append(f"- ⚠️ не покрыты шарды: {', '.join(failed)}")
    lines += [
        "",
        "<details><summary>Сырые данные</summary>",
        "",
        "```json",
        json.dumps(stats, ensure_ascii=False, indent=2),
        "```",
        "",
        "</details>",
    ]
    return "\n".join(lines)


def load_stats(stats_json: str | None) -> dict:
    if not stats_json or not Path(stats_json).exists():
        return {}
    return json.loads(Path(stats_json).read_text(encoding="utf-8"))


def publish(
    db_gz: str,
    sha256: str,
    asset_name: str,
    stats_json: str | None,
    label: str,
    tag: str,
) -> None:
    stats = load_stats(stats_json)
    section = human_summary(label, stats) if stats else f"## {label}\n\n(нет данных о проходе)"

    db_asset = Path(db_gz)
    sha_asset = Path(sha256)
    # gh release upload требует конкретное имя ассета — переименовываем
    # локальные временные копии под нужные имена перед аплоадом.
    upload_paths = []
    if db_asset.name != asset_name:
        renamed = db_asset.with_name(asset_name)
        renamed.write_bytes(db_asset.read_bytes())
        upload_paths.append(str(renamed))
    else:
        upload_paths.append(str(db_asset))
    sha_name = f"{asset_name}.sha256"
    if sha_asset.name != sha_name:
        renamed_sha = sha_asset.with_name(sha_name)
        renamed_sha.write_bytes(sha_asset.read_bytes())
        upload_paths.append(str(renamed_sha))
    else:
        upload_paths.append(str(sha_asset))

    if release_exists(tag):
        logger.info("Релиз %s уже существует — дополняю ассетами и заметками", tag)
        current = run_gh(["release", "view", tag, "--json", "body", "-q", ".body"]).stdout
        new_body = f"{current.rstrip()}\n\n---\n\n{section}\n" if current.strip() else section
        run_gh(["release", "upload", tag, *upload_paths, "--clobber"])
        _edit_notes(tag, new_body)
    else:
        logger.info("Создаю новый снапшот-релиз %s", tag)
        run_gh(
            [
                "release",
                "create",
                tag,
                *upload_paths,
                "--title",
                f"Снапшот {tag.removeprefix(f'{TAG_PREFIX}-')}",
                "--notes",
                section,
            ]
        )


def _edit_notes(tag: str, body: str) -> None:
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(body)
        notes_path = fh.name
    run_gh(["release", "edit", tag, "--notes-file", notes_path])


def rotate(keep_days: int) -> None:
    """Удаляет снапшоты старше ``keep_days``, кроме воскресных — fail-soft."""
    try:
        result = run_gh(["release", "list", "--json", "tagName", "--limit", "500"])
        releases = json.loads(result.stdout)
    except Exception:
        logger.exception("Не удалось получить список релизов для ротации — пропускаю")
        return

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=keep_days)
    for rel in releases:
        tag = rel.get("tagName", "")
        if not tag.startswith(f"{TAG_PREFIX}-"):
            continue
        date_str = tag.removeprefix(f"{TAG_PREFIX}-")
        try:
            tag_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if tag_date >= cutoff:
            continue
        if tag_date.weekday() == 6:  # воскресенье — хранить вечно
            continue
        try:
            run_gh(["release", "delete", tag, "--yes", "--cleanup-tag"])
            logger.info("Удалён устаревший снапшот %s", tag)
        except Exception:
            logger.exception("Не удалось удалить снапшот %s — пропускаю", tag)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Публикует датированный снапшот-релиз базы")
    parser.add_argument("--db-gz", required=True, help="путь к сжатой базе (.db.gz)")
    parser.add_argument("--sha256", required=True, help="путь к файлу с sha256 базы")
    parser.add_argument("--asset-name", required=True, help="имя ассета в релизе, напр. krisha.db.gz")
    parser.add_argument("--stats-json", help="путь к JSON со счётчиками прохода (--summary-json рескрейпа)")
    parser.add_argument("--label", default="Рескрейп", help="заголовок секции в описании релиза")
    parser.add_argument("--tag", help="тег релиза (по умолчанию snapshot-<сегодня UTC>)")
    parser.add_argument("--rotate-keep-days", type=int, default=14, help="хранить снапшоты N дней (0 — не ротировать)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    tag = args.tag or f"{TAG_PREFIX}-{datetime.now(timezone.utc).date().isoformat()}"
    try:
        publish(args.db_gz, args.sha256, args.asset_name, args.stats_json, args.label, tag)
    except subprocess.CalledProcessError as exc:
        logger.error("Публикация снапшота %s провалилась: %s", tag, exc.stderr)
        return 1

    if args.rotate_keep_days > 0:
        rotate(args.rotate_keep_days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
