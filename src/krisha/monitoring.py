"""Мониторинг качества модели (задача 6 бэклога).

Две части:
1. история метрик — `models/metrics_history.jsonl`, по строке на переобучение
   (коммитится retrain-workflow вместе с моделью) → видно тренд MAE/MAPE;
2. Telegram-отчёт после еженедельного retrain: метрики, дельта к базе
   сравнения гейта, вердикт гейта. Чат берётся из env `TG_ADMIN_CHAT_ID` —
   личный чат владельца, не подписчики.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from krisha.config import MODELS_DIR

logger = logging.getLogger(__name__)

METRICS_HISTORY_PATH = MODELS_DIR / "metrics_history.jsonl"
ADMIN_CHAT_ENV = "TG_ADMIN_CHAT_ID"


def append_metrics_history(metrics: dict, path: Path | None = None) -> dict:
    """Добавляет строку истории по свежим метрикам train(). Возвращает запись."""
    entry = {
        "trained_at": metrics.get("trained_at")
        or datetime.now(timezone.utc).isoformat(),
        "mae": round(metrics["model"]["mae"]),
        "mape": round(metrics["model"]["mape"], 4),
        "r2": round(metrics["model"]["r2"], 4),
        "n_train": metrics.get("n_train"),
        "n_test": metrics.get("n_test"),
    }
    path = path or METRICS_HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_metrics_history(path: Path | None = None, last: int = 8) -> list[dict]:
    path = path or METRICS_HISTORY_PATH
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines[-last:]]


def dataset_summary(db_path: Path | str | None = None) -> dict | None:
    """Сводка датасета для отчёта о retrain. None, если базы нет.

    Считает: сколько всего/активных квартир, приток и отток за неделю,
    разбивку активных по комнатам, топ районов, медианы цены и ₸/м².
    """
    from statistics import median

    from krisha.config import DB_PATH
    from krisha.db import get_conn

    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        return None
    with get_conn(path) as conn:
        total, active = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(is_active), 0) FROM listings"
        ).fetchone()
        if not total:
            return None
        new_7d = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE first_seen >= datetime('now', '-7 days')"
        ).fetchone()[0]
        gone_7d = conn.execute(
            "SELECT COUNT(*) FROM listings "
            "WHERE is_active = 0 AND delisted_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        by_rooms: dict[str, int] = {}
        for rooms, cnt in conn.execute(
            "SELECT rooms, COUNT(*) FROM listings "
            "WHERE is_active = 1 AND rooms IS NOT NULL GROUP BY rooms"
        ):
            key = f"{rooms}к" if rooms < 4 else "4к+"
            by_rooms[key] = by_rooms.get(key, 0) + cnt
        top_districts = conn.execute(
            "SELECT district, COUNT(*) AS n FROM listings "
            "WHERE is_active = 1 AND district IS NOT NULL AND district != '' "
            "GROUP BY district ORDER BY n DESC LIMIT 5"
        ).fetchall()
        rows = conn.execute(
            "SELECT price, area FROM listings "
            "WHERE is_active = 1 AND price > 0 AND area > 0"
        ).fetchall()
    prices = [r[0] for r in rows]
    ppsm = [r[0] / r[1] for r in rows]
    return {
        "total": total,
        "active": active,
        "new_7d": new_7d,
        "gone_7d": gone_7d,
        "by_rooms": dict(sorted(by_rooms.items())),
        "top_districts": [(d, n) for d, n in top_districts],
        "median_price": int(median(prices)) if prices else None,
        "median_ppsm": int(median(ppsm)) if ppsm else None,
    }


def format_dataset_block(ds: dict) -> list[str]:
    """Строки блока «данные» для отчёта о retrain (HTML для Telegram)."""

    def _fmt(n: int) -> str:
        return f"{n:,}".replace(",", " ")

    lines = [
        f"📦 <b>Данные</b>: {_fmt(ds['total'])} квартир в базе, "
        f"из них активных {_fmt(ds['active'])}",
        f"За неделю: +{_fmt(ds['new_7d'])} новых, −{_fmt(ds['gone_7d'])} ушло с рынка",
    ]
    if ds.get("by_rooms"):
        lines.append(
            "По комнатам: "
            + " · ".join(f"{k} {_fmt(v)}" for k, v in ds["by_rooms"].items())
        )
    if ds.get("top_districts"):
        lines.append(
            "Топ районов: "
            + " · ".join(f"{d} {_fmt(n)}" for d, n in ds["top_districts"])
        )
    if ds.get("median_price"):
        lines.append(
            f"Медианная цена: {ds['median_price'] / 1e6:.1f} млн ₸ "
            f"({_fmt(ds['median_ppsm'])} ₸/м²)"
        )
    return lines


def format_retrain_report(
    old_meta: dict,
    new_meta: dict,
    gate_passed: bool,
    history: list[dict] | None = None,
    dataset: dict | None = None,
) -> str:
    """HTML-сообщение для Telegram: метрики нового обучения и дельты."""
    previous, new = old_meta["metrics"]["model"], new_meta["metrics"]["model"]
    same_test_old = new_meta["metrics"].get("old_model")
    comparison = same_test_old or previous
    delta_label = "на одном тесте" if same_test_old else "к прошлой"
    mae_delta = (new["mae"] / comparison["mae"] - 1) * 100 if comparison["mae"] else 0.0
    mape_delta = (new["mape"] - comparison["mape"]) * 100

    head = "✅ Модель обновлена" if gate_passed else "🚨 Гейт не пройден — осталась старая модель"
    lines = [
        f"<b>{head}</b> (еженедельный retrain)",
        "",
        f"MAE: <b>{new['mae'] / 1e6:.2f} млн ₸</b> ({mae_delta:+.1f}% {delta_label})",
        f"MAPE: <b>{new['mape']:.1%}</b> ({mape_delta:+.2f} п.п. {delta_label})",
        f"R²: <b>{new['r2']:.3f}</b>",
        f"Обучение: {new_meta['metrics'].get('n_train', '?')} лотов, "
        f"тест: {new_meta['metrics'].get('n_test', '?')}",
    ]
    if same_test_old:
        lines.append(
            "Прошлая мета (другой test): "
            f"MAE {previous['mae'] / 1e6:.2f} млн ₸ · MAPE {previous['mape']:.1%}"
        )
    if history and len(history) >= 2:
        trend = " → ".join(f"{h['mae'] / 1e6:.2f}" for h in history)
        lines += ["", f"Тренд MAE (млн ₸): {trend}"]
    if dataset:
        lines += [""] + format_dataset_block(dataset)
    return "\n".join(lines)


def notify_retrain(old_meta: dict, new_meta: dict, gate_passed: bool) -> bool:
    """Шлёт отчёт в Telegram. False — не отправлено (нет токена/чата)."""
    from krisha.bot import tg_call

    chat_id = os.environ.get(ADMIN_CHAT_ENV)
    if not chat_id:
        logger.info("%s не задан — отчёт о retrain не отправляем", ADMIN_CHAT_ENV)
        return False
    try:
        dataset = dataset_summary()
    except Exception:  # noqa: BLE001 — статистика не должна ронять отчёт
        logger.exception("Не удалось собрать сводку датасета")
        dataset = None
    text = format_retrain_report(
        old_meta, new_meta, gate_passed, history=load_metrics_history(), dataset=dataset
    )
    resp = tg_call("sendMessage", chat_id=int(chat_id), text=text, parse_mode="HTML")
    return bool(resp)
