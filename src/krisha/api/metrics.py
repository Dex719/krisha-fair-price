"""Счётчики горячего пути в памяти процесса.

Зачем свои, а не готовый prometheus-клиент: единственный способ узнать, что
происходило в пик, — это спросить сам сервис. Долбить прод нагрузкой нельзя
(HF банит по IP), внешнего мониторинга нет, а логи uvicorn на Space живут до
рестарта контейнера. После наплыва хочется увидеть три вещи: сколько запросов
пришло на каждую ручку, какой была задержка (p50/p95/p99) и сколько людей
получили отказ (429/503). Всё это стоит одного словаря и кольцевого буфера на
маршрут.

Данные — per-process: при ``WEB_CONCURRENCY=2`` ручка ``/api/metrics`` покажет
статистику ТОГО воркера, которому достался запрос. Для «примерно понять
масштаб» этого достаточно, для точного учёта — нет (и не надо).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

# 512 замеров на маршрут: при 400 rps это последние ~1–2 секунды пика, при
# обычном трафике — последние часы. Памяти — десятки килобайт.
WINDOW = 512

_lock = threading.Lock()
_counters: dict[str, int] = defaultdict(int)
_requests: dict[str, int] = defaultdict(int)
_statuses: dict[str, int] = defaultdict(int)
_latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=WINDOW))
_started = time.time()


def bump(name: str, amount: int = 1) -> None:
    """Произвольный счётчик: rate_limited, predict_busy, predict_cache_hit…"""
    with _lock:
        _counters[name] += amount


def observe(route: str, status: int, duration_ms: float) -> None:
    with _lock:
        _requests[route] += 1
        _statuses[f"{status // 100}xx"] += 1
        _latencies[route].append(duration_ms)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def snapshot() -> dict:
    with _lock:
        routes = {}
        for route, count in sorted(_requests.items(), key=lambda kv: -kv[1]):
            values = list(_latencies.get(route, ()))
            routes[route] = {
                "count": count,
                "p50_ms": _percentile(values, 0.50),
                "p95_ms": _percentile(values, 0.95),
                "p99_ms": _percentile(values, 0.99),
            }
        return {
            "uptime_s": int(time.time() - _started),
            "requests": sum(_requests.values()),
            "statuses": dict(sorted(_statuses.items())),
            "counters": dict(sorted(_counters.items())),
            "routes": routes,
        }


def reset() -> None:
    """Только для тестов."""
    global _started
    with _lock:
        _counters.clear()
        _requests.clear()
        _statuses.clear()
        _latencies.clear()
        _started = time.time()
