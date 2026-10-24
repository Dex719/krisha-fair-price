"""Состояние прохода вне SQLite: серия банов и номер прохода (аудит 24.10).

Зачем. ``ban_streak:<deal>`` и ``pass_seq:<deal>`` живут в таблице
``sweep_state`` базы, а база уезжает в релиз ТОЛЬКО после удачного прохода.
Проход с баном падает (--fail-empty / --fail-below) — и его +1 к серии банов
не доезжает никуда: следующий раннер снова стартует с нулём, откат пауз по
серии банов (pass_plan.BAN_ROLLBACK_STREAK) не срабатывает никогда. С номером
прохода то же: упавший проход не сдвигает ротацию шардов, и следующий
повторяет то же смещение — обрезает тот же хвост.

Поэтому эти ключи дополнительно пишутся маленьким JSON-ассетом
``sweep_state_<deal>.json`` в релиз ``db-latest`` шагом с ``if: always()``,
а при старте вливаются обратно в базу:

- ``pass_seq`` — максимум из базы и файла (счётчик монотонный);
- ``ban_streak`` — значение с более поздним ``updated_at``.

CLI:
    python -m krisha.scraping.sweep_state_file export --deal prodazha \\
        --db data/krisha.db --out /tmp/sweep_state_prodazha.json
    python -m krisha.scraping.sweep_state_file pull --deal prodazha --db data/krisha.db

Заливка файла — scripts/release_upload.py (без окна «файла нет»).
``pull`` fail-soft: нет ассета (первый запуск) или сеть — проход идёт на
значениях из базы, как раньше.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import tempfile
from pathlib import Path

from krisha.db import get_conn, init_db

logger = logging.getLogger(__name__)

KEYS = ("pass_seq", "ban_streak")


def asset_name(deal: str) -> str:
    return f"sweep_state_{deal}.json"


def _rows(conn: sqlite3.Connection, deal: str) -> dict[str, dict]:
    out = {}
    for key in KEYS:
        row = conn.execute(
            "SELECT value, updated_at FROM sweep_state WHERE key = ?", (f"{key}:{deal}",)
        ).fetchone()
        if row is not None:
            out[key] = {"value": str(row[0]), "updated_at": row[1]}
    return out


def export_state(db_path: Path | str, deal: str) -> dict:
    init_db(db_path)
    with get_conn(db_path) as conn:
        keys = _rows(conn, deal)
    return {"deal": deal, "keys": keys}


def import_state(db_path: Path | str, deal: str, payload: dict) -> dict[str, str]:
    """Влить состояние из файла в базу. Возвращает применённые {ключ: значение}."""
    if payload.get("deal") not in (None, deal):
        raise ValueError(f"файл состояния для {payload.get('deal')}, а проход — {deal}")
    init_db(db_path)
    applied: dict[str, str] = {}
    remote = payload.get("keys") or {}
    with get_conn(db_path) as conn:
        local = _rows(conn, deal)
        for key in KEYS:
            r = remote.get(key)
            if not r or r.get("value") is None:
                continue
            try:
                r_value = int(r["value"])
            except (TypeError, ValueError):
                continue
            r_at = r.get("updated_at") or ""
            loc = local.get(key)
            if key == "pass_seq":
                take = loc is None or r_value > int(loc["value"])
            else:
                take = loc is None or r_at > (loc.get("updated_at") or "")
            if not take or (loc is not None and int(loc["value"]) == r_value):
                continue
            conn.execute(
                "INSERT INTO sweep_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (f"{key}:{deal}", str(r_value), r_at or None),
            )
            applied[key] = str(r_value)
    if applied:
        logger.info("Состояние прохода из файла применено: %s", applied)
    return applied


def pull(db_path: Path | str, deal: str) -> dict[str, str]:
    """Скачать sweep_state_<deal>.json из db-latest и влить в базу. Fail-soft."""
    from krisha import db_release

    try:
        url, headers, _, _ = db_release._asset_source(
            db_release.RELEASE_TAG, asset_name(deal), "KRISHA_SWEEP_STATE_URL"
        )
    except FileNotFoundError:
        logger.info("Файла состояния %s в релизе нет — беру значения из базы", asset_name(deal))
        return {}
    except Exception:  # noqa: BLE001 — состояние вторично, проход важнее
        logger.warning("Не удалось найти файл состояния прохода — беру значения из базы",
                       exc_info=True)
        return {}
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / asset_name(deal)
            db_release._fetch(url, headers, dest)
            payload = json.loads(dest.read_text(encoding="utf-8"))
        return import_state(db_path, deal, payload)
    except Exception:  # noqa: BLE001
        logger.warning("Файл состояния прохода не применён — беру значения из базы",
                       exc_info=True)
        return {}


def main(argv: list[str] | None = None) -> int:
    from krisha.config import DB_PATH, RENT_DB_PATH

    parser = argparse.ArgumentParser(description="Состояние прохода (баны, ротация) вне базы")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("export", "import", "pull"):
        p = sub.add_parser(name)
        p.add_argument("--deal", choices=["prodazha", "arenda"], default="prodazha")
        p.add_argument("--db", help="путь к базе (по умолчанию — по --deal)")
        if name == "export":
            p.add_argument("--out", required=True)
        if name == "import":
            p.add_argument("--file", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = Path(args.db) if args.db else Path(RENT_DB_PATH if args.deal == "arenda" else DB_PATH)

    if args.cmd == "export":
        if not db_path.exists():
            print(f"базы {db_path} нет — экспортировать нечего", file=sys.stderr)
            return 1
        payload = export_state(db_path, args.deal)
        out = Path(args.out)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"состояние прохода → {out}: {payload['keys']}")
        return 0
    if args.cmd == "import":
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        print(f"применено: {import_state(db_path, args.deal, payload)}")
        return 0
    print(f"применено: {pull(db_path, args.deal)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
