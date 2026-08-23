"""Кэш ответов API: TTL + single-flight + отдача протухшего при сбое.

Зачем отдельный примитив, а не `dict` с меткой времени (как было у /api/stats):

* **Стампед.** Пока значение свежее, все запросы читают память. В момент
  истечения TTL под нагрузкой в тяжёлую функцию проваливаются ВСЕ запросы
  разом: сто параллельных читателей — сто пересчётов статистики по базе.
  Здесь пересчёт делает ровно один поток (per-key lock), остальные ждут его
  результат.
* **Провал на секунду.** Если пересчёт упал (база занята, файл подменяется
  скрейпером), лучше отдать чуть протухшее значение, чем 503 всем подряд.
  ``stale_ttl`` задаёт, как долго это допустимо.

Кэш живёт в памяти процесса: при нескольких воркерах uvicorn у каждого свой
(это нормально — данные одинаковые, просто пересчётов будет по одному на
воркер).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Hashable

logger = logging.getLogger(__name__)


class TTLCache:
    """Потокобезопасный кэш «значение живёт ttl секунд».

    ``ttl`` — сколько значение считается свежим.
    ``stale_ttl`` — сколько ещё можно отдавать протухшее значение, если
    пересчёт упал с исключением (None — не отдавать, пробрасывать ошибку).
    ``maxsize`` — потолок числа ключей: кэш ключей от пользовательского ввода
    (id лота) иначе растёт неограниченно.
    """

    def __init__(self, ttl: float, *, stale_ttl: float | None = None, maxsize: int = 512) -> None:
        self.ttl = ttl
        self.stale_ttl = stale_ttl
        self.maxsize = maxsize
        self._values: dict[Hashable, tuple[float, Any]] = {}
        self._locks: dict[Hashable, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- служебное -----------------------------------------------------
    def _key_lock(self, key: Hashable) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock

    def _evict_if_needed(self) -> None:
        """Вызывается под self._guard. Выкидываем самые старые записи."""
        if len(self._values) <= self.maxsize:
            return
        overflow = len(self._values) - self.maxsize
        for key, _ in sorted(self._values.items(), key=lambda kv: kv[1][0])[:overflow]:
            self._values.pop(key, None)
            self._locks.pop(key, None)

    def peek(self, key: Hashable) -> tuple[float, Any] | None:
        """(возраст в секундах, значение) или None, если ключа нет."""
        with self._guard:
            entry = self._values.get(key)
        if entry is None:
            return None
        ts, value = entry
        return time.monotonic() - ts, value

    def set(self, key: Hashable, value: Any) -> None:
        with self._guard:
            self._values[key] = (time.monotonic(), value)
            self._evict_if_needed()

    def clear(self) -> None:
        with self._guard:
            self._values.clear()
            self._locks.clear()

    def __len__(self) -> int:
        return len(self._values)

    # -- основное ------------------------------------------------------
    def get_or_call(self, key: Hashable, producer: Callable[[], Any]) -> Any:
        """Свежее значение из кэша, иначе один вызов producer на все потоки."""
        fresh = self.peek(key)
        if fresh is not None and fresh[0] < self.ttl:
            return fresh[1]

        with self._key_lock(key):
            # Пока ждали лок, значение мог посчитать сосед.
            again = self.peek(key)
            if again is not None and again[0] < self.ttl:
                return again[1]
            try:
                value = producer()
            except Exception:
                stale = self.peek(key)
                if (
                    stale is not None
                    and self.stale_ttl is not None
                    and stale[0] < self.ttl + self.stale_ttl
                ):
                    logger.warning(
                        "кэш %r: пересчёт упал, отдаём протухшее значение (%.0f с)",
                        key,
                        stale[0],
                        exc_info=True,
                    )
                    return stale[1]
                raise
            self.set(key, value)
            return value
