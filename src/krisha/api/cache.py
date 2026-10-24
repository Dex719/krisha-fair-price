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
* **Очередь за пересчётом.** С ``serve_stale_while_refreshing`` протухшее
  значение отдаётся сразу, пока один поток его пересчитывает: читатели
  /api/stats не стоят секунды в очереди за чужим пересчётом.

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


class _KeyLock:
    """Лок одного ключа + число потоков, которые его держат или ждут.

    Счётчик нужен, чтобы убирать лок из словаря, как только он никому не
    нужен. Раньше локи жили до вытеснения значения, а при ошибке producer
    значения не было вовсе — и каждый мусорный ключ оставлял лок навсегда
    (аудит 24.10: 10 000 битых ссылок → 10 000 локов до рестарта).
    """

    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


class TTLCache:
    """Потокобезопасный кэш «значение живёт ttl секунд».

    ``ttl`` — сколько значение считается свежим.
    ``stale_ttl`` — сколько ещё можно отдавать протухшее значение, если
    пересчёт упал с исключением (None — не отдавать, пробрасывать ошибку).
    ``maxsize`` — потолок числа ключей: кэш ключей от пользовательского ввода
    (id лота) иначе растёт неограниченно.
    ``serve_stale_while_refreshing`` — stale-while-revalidate: пока ОДИН поток
    пересчитывает протухшее значение, остальные сразу получают протухшее, а не
    ждут его (пересчёт /api/stats — секунды, и раньше все читатели стояли в
    очереди за ним). Действует только в окне ``stale_ttl``.
    """

    def __init__(
        self,
        ttl: float,
        *,
        stale_ttl: float | None = None,
        maxsize: int = 512,
        serve_stale_while_refreshing: bool = False,
    ) -> None:
        self.ttl = ttl
        self.stale_ttl = stale_ttl
        self.maxsize = maxsize
        self.serve_stale_while_refreshing = serve_stale_while_refreshing
        self._values: dict[Hashable, tuple[float, Any]] = {}
        self._locks: dict[Hashable, _KeyLock] = {}
        self._guard = threading.Lock()

    # -- служебное -----------------------------------------------------
    def _enter(self, key: Hashable) -> _KeyLock:
        """Берёт (или заводит) лок ключа и отмечается в нём. Пара — _leave."""
        with self._guard:
            entry = self._locks.get(key)
            if entry is None:
                entry = self._locks[key] = _KeyLock()
            entry.users += 1
            return entry

    def _leave(self, key: Hashable, entry: _KeyLock) -> None:
        """Последний ушедший убирает лок из словаря — при успехе и при ошибке.

        Пока лок кто-то держит или ждёт (users > 0), он остаётся на месте:
        иначе новый поток завёл бы второй лок на тот же ключ, и single-flight
        развалился бы. Проверка ``is entry`` — clear() мог уже выкинуть этот
        лок, и под ключом живёт чужой.
        """
        with self._guard:
            entry.users -= 1
            if entry.users <= 0 and self._locks.get(key) is entry:
                del self._locks[key]

    def _evict_if_needed(self) -> None:
        """Вызывается под self._guard. Выкидываем самые старые записи.

        Локи не трогаем: их жизнь определяет счётчик users, а выкинуть лок,
        который кто-то держит, значило бы пустить второй пересчёт параллельно.
        """
        if len(self._values) <= self.maxsize:
            return
        overflow = len(self._values) - self.maxsize
        for key, _ in sorted(self._values.items(), key=lambda kv: kv[1][0])[:overflow]:
            self._values.pop(key, None)

    def _stale_usable(self, entry: tuple[float, Any] | None) -> bool:
        return (
            entry is not None
            and self.stale_ttl is not None
            and entry[0] < self.ttl + self.stale_ttl
        )

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
    def get_or_call(
        self,
        key: Hashable,
        producer: Callable[[], Any],
        *,
        recheck: Callable[[], None] | None = None,
    ) -> Any:
        """Свежее значение из кэша, иначе один вызов producer на все потоки.

        ``recheck`` зовётся под локом ключа прямо перед producer и может
        бросить исключение вместо вызова. Нужен калитке предикта: пока поток
        ждал лок, сосед мог получить отказ источника, и повторять тот же
        скрейп следом за ним незачем (аудит 24.10, predict_gate).
        """
        fresh = self.peek(key)
        if fresh is not None and fresh[0] < self.ttl:
            return fresh[1]

        entry = self._enter(key)
        try:
            if self.serve_stale_while_refreshing and self._stale_usable(fresh):
                # Пересчёт уже идёт в соседнем потоке — не ждём, отдаём протухшее.
                if not entry.lock.acquire(blocking=False):
                    return fresh[1]  # type: ignore[index]
            else:
                entry.lock.acquire()
            try:
                return self._produce(key, producer, recheck)
            finally:
                entry.lock.release()
        finally:
            self._leave(key, entry)

    def _produce(
        self, key: Hashable, producer: Callable[[], Any], recheck: Callable[[], None] | None
    ) -> Any:
        """Вызывается под локом ключа."""
        # Пока ждали лок, значение мог посчитать сосед.
        again = self.peek(key)
        if again is not None and again[0] < self.ttl:
            return again[1]
        if recheck is not None:
            recheck()
        try:
            value = producer()
        except Exception:
            stale = self.peek(key)
            if self._stale_usable(stale):
                logger.warning(
                    "кэш %r: пересчёт упал, отдаём протухшее значение (%.0f с)",
                    key,
                    stale[0],  # type: ignore[index]
                    exc_info=True,
                )
                return stale[1]  # type: ignore[index]
            raise
        self.set(key, value)
        return value
