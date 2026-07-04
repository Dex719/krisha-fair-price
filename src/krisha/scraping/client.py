"""Вежливый HTTP-клиент: паузы 2–4 сек, настраиваемые ретраи, единый User-Agent."""

import logging
import random
import time

import httpx

from krisha.config import MAX_RETRIES, REQUEST_DELAY_RANGE, REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)


class PoliteClient:
    """Обёртка над httpx.Client с паузой перед каждым запросом и ретраями.

    max_retries/throttle_wait_s настраиваются по контексту: батч-краулер
    может позволить себе длинные бэкоффы (дефолты), а пользовательский путь
    (/api/predict, бот) обязан ответить за секунды — иначе долгие sleep'ы
    держат поток тредпула и под нагрузкой вешают весь сервис.
    """

    def __init__(
        self,
        delay_range: tuple[float, float] = REQUEST_DELAY_RANGE,
        max_retries: int = MAX_RETRIES,
        throttle_wait_s: float = 30.0,
    ):
        self.delay_range = delay_range
        self.max_retries = max(1, int(max_retries))
        self.throttle_wait_s = throttle_wait_s
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    def get(self, url: str) -> str | None:
        """GET с паузой и ретраями. Возвращает HTML или None при неудаче."""
        for attempt in range(1, self.max_retries + 1):
            time.sleep(random.uniform(*self.delay_range))
            try:
                resp = self._client.get(url)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 404:
                    logger.warning("404: %s", url)
                    return None
                if resp.status_code in (403, 429):
                    # Нас тормозят — ждём (сколько позволяет контекст) и пробуем ещё
                    wait = self.throttle_wait_s * attempt
                    logger.warning("HTTP %s на %s, ждём %s сек", resp.status_code, url, wait)
                    if attempt < self.max_retries:
                        time.sleep(wait)
                    continue
                logger.warning("HTTP %s: %s", resp.status_code, url)
            except httpx.HTTPError as exc:
                logger.warning("Ошибка запроса %s (попытка %s): %s", url, attempt, exc)
        return None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()
