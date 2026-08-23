"""Долговечный лог предиктов: то, ради чего заводился #128.

Проблема, которую он закрывает. Таблица `predictions` живёт в SQLite, а база
запекается в Docker-образ и пересобирается каждую ночь: всё, что рантайм
записал за день, стирается при следующем деплое. В живой базе на 141k строк
`price_history` предиктов ровно 0 — то есть продуктовую метрику «совпадает ли
вердикт с последующей судьбой лота» проверить не на чем.

Решение нарочно самое дешёвое из работающих: те же грабли, что уже держат
подписки и статистику, — JSON-файл в самом репозитории через Contents API
(см. subscriptions.save_json_state). Никакой внешней СУБД: на текущем трафике
(единицы предиктов в день) это десятки килобайт в год.

Формат — ПЛОСКИЙ словарь `ключ -> строка`, где ключ = "<время>|<id лота>".
Ровно так работает слияние конкурентных записей в subscriptions._merge_remote:
объединение по ключам верхнего уровня. Вложи мы список в {"rows": [...]},
второй воркер затирал бы записи первого.

Приватность: id объявления и цены — публичные данные, PII здесь нет, поэтому
файл пишется открытым текстом (encrypt=False), как usage_stats.json.

В GitHub Actions лог не ведём: там предикты пакетные (алерты по всем свежим
лотам), их сотни за прогон, и место им в базе, а не в git-истории.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from krisha.config import DATA_DIR

logger = logging.getLogger(__name__)

LOG_PATH = DATA_DIR / "prediction_log.json"
# Флашим не чаще раза в интервал: коммит на каждый предикт — это коммит на
# каждое действие пользователя. Интервал короче, чем у usage-статистики:
# предиктов единицы в день, и терять их при рестарте обиднее, чем визиты.
FLUSH_INTERVAL = timedelta(minutes=5)
# Потолок на размер файла. При нынешнем трафике недостижим годами; страхует от
# сценария «кто-то нашёл сервис» — тогда старые записи вытесняются новыми.
MAX_ROWS = 20_000

_state: dict | None = None
_last_flush: datetime | None = None
_lock = threading.Lock()


def _enabled() -> bool:
    """Ведём лог только там, где он переживёт рестарт.

    По умолчанию ВЫКЛЮЧЕНО, включается переменной PREDICTION_LOG=1 в
    окружении Space. Так сделано нарочно: каждая запись — это коммит в
    репозиторий, а деплой пересобирает Space на любой коммит, кроме
    перечисленных в paths-ignore. Пока data/prediction_log.json не добавлен
    в этот список (см. ops/github-workflows/deploy-hf.yml), включённый лог
    пересобирал бы Space каждые несколько минут.

    В GitHub Actions выключено всегда: там предикты пакетные (алерты по всем
    свежим лотам), их сотни за прогон, и место им в базе, а не в git-истории.
    """
    explicit = os.environ.get("PREDICTION_LOG", "auto").strip().lower()
    if explicit in {"0", "off", "false"}:
        return False
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return False
    return explicit in {"1", "on", "true"}


def load_state() -> dict:
    from krisha.subscriptions import load_json_state

    state = load_json_state(LOG_PATH)
    return state if isinstance(state, dict) else {}


def record(
    listing_id,
    fair_price: float | None,
    fair_low: float | None,
    fair_high: float | None,
    verdict: str | None,
    model_version: str | None,
    now: datetime | None = None,
) -> None:
    """Регистрирует предикт. Никогда не бросает исключений."""
    if not _enabled() or listing_id is None:
        return
    try:
        _record(listing_id, fair_price, fair_low, fair_high, verdict, model_version, now)
    except Exception:  # noqa: BLE001 — лог предикта не должен ломать оценку
        logger.exception("Не удалось записать предикт в долговечный лог")


def _record(
    listing_id,
    fair_price: float | None,
    fair_low: float | None,
    fair_high: float | None,
    verdict: str | None,
    model_version: str | None,
    now: datetime | None = None,
) -> None:
    global _state, _last_flush

    now = now or datetime.now(timezone.utc)
    row = {
        "listing_id": int(listing_id),
        "at": now.isoformat(timespec="milliseconds"),
        "fair_price": _num(fair_price),
        "fair_low": _num(fair_low),
        "fair_high": _num(fair_high),
        "verdict": verdict,
        "model": model_version,
    }
    key = f"{row['at']}|{row['listing_id']}"

    # Тот же лок, что и у usage: хендлеры FastAPI живут в тредпуле, а флаш
    # ходит по сети — держать лок на сетевом вызове нельзя (все запросы
    # выстроятся в очередь за api.github.com), поэтому снимаем снапшот и
    # выходим из-под лока до флаша.
    with _lock:
        if _state is None:
            _state = load_state()
        _state[key] = row
        _prune(_state)
        due = _last_flush is None or now - _last_flush >= FLUSH_INTERVAL
        if not due:
            return
        _last_flush = now
        snapshot = copy.deepcopy(_state)
    _flush_async(snapshot)


def _num(value) -> float | None:
    try:
        return round(float(value), 2) if value is not None else None
    except (TypeError, ValueError):
        return None


def _prune(state: dict) -> None:
    """Оставляет MAX_ROWS свежих записей. Ключ начинается с ISO-времени,
    поэтому лексикографическая сортировка = хронологическая."""
    if len(state) <= MAX_ROWS:
        return
    for key in sorted(state)[: len(state) - MAX_ROWS]:
        state.pop(key, None)


def _flush_async(snapshot: dict) -> None:
    if os.environ.get("USAGE_FLUSH_SYNC") == "1":  # тесты и CLI
        _flush(snapshot)
        return
    threading.Thread(target=_flush, args=(snapshot,), name="prediction-log-flush",
                     daemon=True).start()


def _flush(snapshot: dict) -> None:
    from krisha.subscriptions import save_json_state

    # Локальную копию перечитываем перед слиянием ровно как usage: соседний
    # воркер мог дописать своё, пока мы копили.
    merged = {**load_state(), **snapshot}
    _prune(merged)
    save_json_state(LOG_PATH, merged, "data: лог предиктов", encrypt=False)


def flush_now() -> None:
    """Принудительный флаш (CLI, тесты)."""
    with _lock:
        snapshot = copy.deepcopy(_state or {})
    if snapshot:
        _flush(snapshot)
