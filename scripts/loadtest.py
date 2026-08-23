"""Нагрузочный замер публичных ручек: latency по одному и потолок в параллель.

Зачем в репозитории: «стало быстрее» — не мнение, а число, и его должно быть
можно повторить через полгода после любой правки. Скрипт не трогает
/api/predict: тот ходит на krisha.kz, долбить чужой сервер синтетикой нельзя.

Запуск против локального сервера:

    KRISHA_DB_AUTO=0 RATE_LIMIT_PER_WINDOW=100000 \\
        uvicorn krisha.api.app:app --port 7801 &
    python scripts/loadtest.py http://127.0.0.1:7801

Против прода (осторожно, там включён rate limit на IP и защита хостинга):

    python scripts/loadtest.py https://dex719-krisha-fair-price.hf.space --workers 4

Вывод — JSON: p50/p95/p99 в миллисекундах, rps и коды ответов.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time

import httpx

DEFAULT_PATHS = ("/api/health", "/api/stats", "/api/demo", "/", "/static/design.css")


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, int(len(values) * share) - 1))
    return round(sorted(values)[index], 1)


async def _measure_serial(client: httpx.AsyncClient, path: str, count: int) -> dict:
    latencies: list[float] = []
    codes: list[int] = []
    for _ in range(count):
        started = time.perf_counter()
        response = await client.get(path)
        latencies.append((time.perf_counter() - started) * 1000)
        codes.append(response.status_code)
    return {
        "p50": round(statistics.median(latencies), 1),
        "p95": _percentile(latencies, 0.95),
        "codes": sorted(set(codes)),
    }


async def _measure_parallel(
    client: httpx.AsyncClient, path: str, workers: int, seconds: float
) -> dict:
    deadline = time.perf_counter() + seconds
    latencies: list[float] = []
    codes: list[int] = []

    async def worker() -> None:
        while time.perf_counter() < deadline:
            started = time.perf_counter()
            response = await client.get(path)
            latencies.append((time.perf_counter() - started) * 1000)
            codes.append(response.status_code)

    started_at = time.perf_counter()
    await asyncio.gather(*[worker() for _ in range(workers)])
    elapsed = time.perf_counter() - started_at
    ok = sum(1 for code in codes if code < 400)
    return {
        "rps": round(ok / elapsed, 1),
        "p50": round(statistics.median(latencies), 1) if latencies else 0.0,
        "p95": _percentile(latencies, 0.95),
        "p99": _percentile(latencies, 0.99),
        "requests": len(latencies),
        "errors": {code: codes.count(code) for code in sorted(set(codes)) if code >= 400},
    }


async def run(args: argparse.Namespace) -> dict:
    limits = httpx.Limits(max_connections=args.workers * 2, max_keepalive_connections=args.workers * 2)
    report: dict = {"base": args.base, "workers": args.workers}
    async with httpx.AsyncClient(base_url=args.base, timeout=args.timeout, limits=limits) as client:
        await client.get("/api/health")  # прогрев соединения и кэшей
        report["serial"] = {
            path: await _measure_serial(client, path, args.count) for path in args.paths
        }
        report["parallel"] = {
            path: await _measure_parallel(client, path, args.workers, args.seconds)
            for path in args.paths
            if not path.startswith("/static")
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="базовый URL, например http://127.0.0.1:7801")
    parser.add_argument("--paths", nargs="*", default=list(DEFAULT_PATHS))
    parser.add_argument("--count", type=int, default=25, help="запросов в последовательном замере")
    parser.add_argument("--workers", type=int, default=20, help="параллельных клиентов")
    parser.add_argument("--seconds", type=float, default=8.0, help="длительность параллельного замера")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
