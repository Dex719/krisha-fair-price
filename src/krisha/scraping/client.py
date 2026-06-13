"""Вежливый HTTP-клиент: паузы 2–4 сек, ретраи, единый User-Agent."""

import logging
import os
import random
import time

import httpx

from krisha.config import MAX_RETRIES, REQUEST_DELAY_RANGE, REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)


class PoliteClient:
    """Обёртка над httpx.Client с паузой перед каждым запросом и ретраями."""

    def __init__(self, delay_range: tuple[float, float] = REQUEST_DELAY_RANGE):
        self.delay_range = delay_range
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    def get(self, url: str) -> str | None:
        """GET с паузой и ретраями. Возвращает HTML или None при неудаче."""
        for attempt in range(1, MAX_RETRIES + 1):
            time.sleep(random.uniform(*self.delay_range))
            try:
                resp = self._client.get(url)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 404:
                    logger.warning("404: %s", url)
                    return None
                if resp.status_code in (403, 429):
                    # Нас тормозят — ждём подольше и пробуем ещё раз
                    wait = 30 * attempt
                    logger.warning("HTTP %s на %s, ждём %s сек", resp.status_code, url, wait)
                    time.sleep(wait)
                    continue
                logger.warning("HTTP %s: %s", resp.status_code, url)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                # Таймаут/обрыв соединения = вероятный троттлинг IP. Эскалируем
                # ожидание, иначе нас банят надолго. Управляется env:
                #   KRISHA_TIMEOUT_BACKOFF  — базовая пауза (сек, по умолч. 60)
                base = float(os.environ.get("KRISHA_TIMEOUT_BACKOFF", "60"))
                wait = base * attempt
                logger.warning(
                    "Сеть %s на %s (попытка %s) — ждём %.0f сек", type(exc).__name__, url, attempt, wait
                )
                time.sleep(wait)
            except httpx.HTTPError as exc:
                logger.warning("Ошибка запроса %s (попытка %s): %s", url, attempt, exc)
        return None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()
