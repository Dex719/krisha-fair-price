"""Учение по восстановлению: реально ли поднять сервис из опубликованной базы.

Зачем. Единственная копия данных живёт в GitHub Release `db-latest`, куда
её каждую ночь заливает рескрейп. Бэкап, который ни разу не разворачивали,
бэкапом не является: битый gzip, оборванная заливка, разошедшийся checksum и
отсутствующая таблица обнаруживаются в тот день, когда база уже нужна.

Что делает (ничего не меняет на диске проекта — всё во временном каталоге):

1. скачивает `krisha.db.gz` из релиза и сверяет sha256 с опубликованным;
2. распаковывает и прогоняет `PRAGMA integrity_check`;
3. проверяет, что схема на месте и данные осмысленны: есть обязательные
   таблицы, объявления и история цен непусты, активных лотов больше нуля;
4. смотрит свежесть: MAX(last_seen) не старше порога (по умолчанию 48 часов);
5. поднимает приложение на этой базе (TestClient) и дёргает /api/health и
   /api/stats — то есть проверяет не «файл скачался», а «сервис на нём живёт».

Запуск:
    python scripts/restore_drill.py                 # полное учение
    python scripts/restore_drill.py --skip-app      # без подъёма приложения
    python scripts/restore_drill.py --max-age-hours 72
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REQUIRED_TABLES = ("listings", "price_history", "sweep_runs")


class DrillError(AssertionError):
    """Учение провалено — восстановление из релиза не работает."""


def _check(label: str, ok: bool, detail: str = "") -> None:
    mark = "✓" if ok else "✗"
    print(f"  {mark} {label}{': ' + detail if detail else ''}")
    if not ok:
        raise DrillError(f"{label}{': ' + detail if detail else ''}")


def restore(db_path: Path) -> None:
    """Скачивание + checksum + распаковка — тем же кодом, что и на проде."""
    from krisha import db_release

    db_release.download(db_path)


def check_database(db_path: Path, max_age_hours: float) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        _check("integrity_check", integrity == "ok", integrity)

        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        _check("схема на месте", not missing, f"нет таблиц: {missing}" if missing else "")

        listings = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        active = con.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1").fetchone()[0]
        history = con.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        _check("объявления есть", listings > 0, f"{listings} строк")
        _check("активные лоты есть", active > 0, f"{active} активных")
        _check("история цен есть", history > 0, f"{history} точек")

        last_seen = con.execute("SELECT MAX(last_seen) FROM listings").fetchone()[0]
        age_hours = _age_hours(last_seen)
        _check(
            "данные свежие",
            age_hours is not None and age_hours <= max_age_hours,
            f"последнее наблюдение {last_seen} ({age_hours:.1f} ч назад)"
            if age_hours is not None
            else "MAX(last_seen) не читается",
        )
        return {"listings": listings, "active": active, "history": history,
                "last_seen": last_seen, "age_hours": age_hours}
    finally:
        con.close()


def _age_hours(last_seen: str | None) -> float | None:
    if not last_seen:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            moment = datetime.strptime(last_seen, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return (datetime.now(timezone.utc) - moment).total_seconds() / 3600
    return None


def check_app(db_path: Path) -> None:
    """Сервис на восстановленной базе, а не просто файл на диске."""
    os.environ["KRISHA_DB_AUTO"] = "0"  # приложение не должно докачивать своё
    from fastapi.testclient import TestClient

    from krisha.api import app as app_module

    app_module.DB_PATH = db_path
    app_module._freshness_cache.clear()
    app_module._stats_cache.clear()

    client = TestClient(app_module.app)
    health = client.get("/api/health")
    _check("GET /api/health = 200", health.status_code == 200, str(health.status_code))
    _check("модель загружена", bool(health.json().get("model_loaded")))
    _check("база не протухла", health.json().get("freshness") == "ok",
           str(health.json().get("freshness")))

    stats = client.get("/api/stats")
    _check("GET /api/stats = 200", stats.status_code == 200, str(stats.status_code))
    total = (stats.json() or {}).get("total_listings")
    _check("статистика непустая", bool(total), f"total_listings={total}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Учение по восстановлению базы из релиза")
    parser.add_argument("--max-age-hours", type=float, default=48.0,
                        help="допустимый возраст последнего наблюдения (по умолчанию 48)")
    parser.add_argument("--skip-app", action="store_true",
                        help="только база, без подъёма приложения")
    parser.add_argument("--keep", metavar="PATH",
                        help="сохранить восстановленную базу по этому пути")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="restore-drill-") as tmpdir:
        db_path = Path(args.keep) if args.keep else Path(tmpdir) / "krisha.db"
        print(f"1/3 Восстановление из релиза db-latest → {db_path}")
        try:
            restore(db_path)
        except Exception as exc:  # noqa: BLE001 — учение обязано сказать, что упало
            print(f"  ✗ скачивание/проверка: {exc}", file=sys.stderr)
            return 1
        size_mb = db_path.stat().st_size / 1e6
        print(f"  ✓ база распакована: {size_mb:.1f} МБ (checksum сверен)")

        print("2/3 Проверки базы")
        try:
            summary = check_database(db_path, args.max_age_hours)
            if not args.skip_app:
                print("3/3 Приложение на восстановленной базе")
                check_app(db_path)
            else:
                print("3/3 Приложение пропущено (--skip-app)")
        except DrillError as exc:
            print(f"\nDRILL FAILED: {exc}", file=sys.stderr)
            return 1

    print(
        "\nDRILL OK: восстановление работает "
        f"({summary['listings']} лотов, {summary['active']} активных, "
        f"данные {summary['age_hours']:.1f} ч)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
