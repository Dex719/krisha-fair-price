"""Мониторинг качества модели (задача 6 бэклога).

Две части:
1. история метрик — `models/metrics_history.jsonl`, по строке на переобучение
   (коммитится retrain-workflow вместе с моделью) → видно тренд MAE/MAPE;
2. Telegram-отчёт после еженедельного retrain: метрики, дельта к прошлой
   модели, вердикт гейта. Чат берётся из env `TG_ADMIN_CHAT_ID` — личный
   чат владельца, не подписчики.
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


def format_retrain_report(
    old_meta: dict, new_meta: dict, gate_passed: bool, history: list[dict] | None = None
) -> str:
    """HTML-сообщение для Telegram: метрики нового обучения и дельты."""
    old, new = old_meta["metrics"]["model"], new_meta["metrics"]["model"]
    mae_delta = (new["mae"] / old["mae"] - 1) * 100 if old["mae"] else 0.0
    mape_delta = (new["mape"] - old["mape"]) * 100

    head = "✅ Модель обновлена" if gate_passed else "🚨 Гейт не пройден — осталась старая модель"
    lines = [
        f"<b>{head}</b> (еженедельный retrain)",
        "",
        f"MAE: <b>{new['mae'] / 1e6:.2f} млн ₸</b> ({mae_delta:+.1f}% к прошлой)",
        f"MAPE: <b>{new['mape']:.1%}</b> ({mape_delta:+.2f} п.п.)",
        f"R²: <b>{new['r2']:.3f}</b>",
        f"Обучение: {new_meta['metrics'].get('n_train', '?')} лотов, "
        f"тест: {new_meta['metrics'].get('n_test', '?')}",
    ]
    if history and len(history) >= 2:
        trend = " → ".join(f"{h['mae'] / 1e6:.2f}" for h in history)
        lines += ["", f"Тренд MAE (млн ₸): {trend}"]
    return "\n".join(lines)


def notify_retrain(old_meta: dict, new_meta: dict, gate_passed: bool) -> bool:
    """Шлёт отчёт в Telegram. False — не отправлено (нет токена/чата)."""
    from krisha.bot import tg_call

    chat_id = os.environ.get(ADMIN_CHAT_ENV)
    if not chat_id:
        logger.info("%s не задан — отчёт о retrain не отправляем", ADMIN_CHAT_ENV)
        return False
    text = format_retrain_report(
        old_meta, new_meta, gate_passed, history=load_metrics_history()
    )
    resp = tg_call("sendMessage", chat_id=int(chat_id), text=text, parse_mode="HTML")
    return bool(resp)
