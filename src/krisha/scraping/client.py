"""Вежливый HTTP-клиент: паузы 2–4 сек, настраиваемые ретраи, единый User-Agent."""

import logging
import random
import time

import httpx

from krisha.config import MAX_RETRIES, REQUEST_DELAY_RANGE, REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)


class BanDetected(RuntimeError):
    """Несколько URL подряд отдали ТОЛЬКО HTTP 403 на всех попытках (issue #101).

    Это отличается от 429 (троттлинг — тем же UA/IP через эскалирующий
    бэкофф обычно снова пускают): серия 403 обычно значит бан по IP/JA3
    (датацентр-диапазоны GitHub Actions публично известны Cloudflare).
    Ждать/ретраить тем же отпечатком бессмысленно — вызывающий код должен
    прервать проход досрочно (early-abort), а не долбить дальше до
    max_pages/следующего шарда, тратя часы впустую.
    """


class PoliteClient:
    """Обёртка над httpx.Client с паузой перед каждым запросом и ретраями.

    max_retries/throttle_wait_s настраиваются по контексту: батч-краулер
    может позволить себе длинные бэкоффы (дефолты), а пользовательский путь
    (/api/predict, бот) обязан ответить за секунды — иначе долгие sleep'ы
    держат поток тредпула и под нагрузкой вешают весь сервис.

    403 и 429 обрабатываются по-разному (issue #101, см. `BanDetected`):
    429 — «притормози», тот же UA/IP переживает обычный эскалирующий
    бэкофф; серия из `ban_streak_threshold` URL подряд, где ВСЕ попытки
    вернули 403, поднимает `BanDetected` — вызывающий код (crawler/sweep)
    должен остановить проход, а не продолжать до конца лимита.
    """

    def __init__(
        self,
        delay_range: tuple[float, float] = REQUEST_DELAY_RANGE,
        max_retries: int = MAX_RETRIES,
        throttle_wait_s: float = 30.0,
        ban_streak_threshold: int = 3,
    ):
        self.delay_range = delay_range
        self.max_retries = max(1, int(max_retries))
        self.throttle_wait_s = throttle_wait_s
        self.ban_streak_threshold = max(1, int(ban_streak_threshold))
        self._ban_streak = 0
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    def get(self, url: str) -> str | None:
        """GET с паузой и ретраями. Возвращает HTML или None при неудаче.

        Поднимает `BanDetected`, если этот URL и предыдущие подряд (см.
        `ban_streak_threshold`) отдали 403 на КАЖДОЙ попытке — см. класс.
        Любой другой исход (успех, 404, 429, сетевая ошибка) сбрасывает
        счётчик серии.
        """
        saw_403 = False
        all_403 = True
        for attempt in range(1, self.max_retries + 1):
            time.sleep(random.uniform(*self.delay_range))
            try:
                resp = self._client.get(url)
                if resp.status_code == 200:
                    self._ban_streak = 0
                    return resp.text
                if resp.status_code == 404:
                    self._ban_streak = 0
                    logger.warning("404: %s", url)
                    return None
                if resp.status_code == 429:
                    # Троттлят, не банят — эскалирующий бэкофф оправдан.
                    all_403 = False
                    wait = self.throttle_wait_s * attempt
                    logger.warning("HTTP 429 (троттлинг) на %s, ждём %s сек", url, wait)
                    if attempt < self.max_retries:
                        time.sleep(wait)
                    continue
                if resp.status_code == 403:
                    saw_403 = True
                    logger.warning(
                        "HTTP 403 на %s (попытка %s) — тем же UA/IP; если это бан, "
                        "ретрай не поможет",
                        url,
                        attempt,
                    )
                    if attempt < self.max_retries:
                        time.sleep(self.throttle_wait_s)
                    continue
                all_403 = False
                logger.warning("HTTP %s: %s", resp.status_code, url)
            except httpx.HTTPError as exc:
                all_403 = False
                logger.warning("Ошибка запроса %s (попытка %s): %s", url, attempt, exc)

        if saw_403 and all_403:
            self._ban_streak += 1
            if self._ban_streak >= self.ban_streak_threshold:
                streak, url_at_streak = self._ban_streak, url
                self._ban_streak = 0  # следующий проход/шард начинает счёт заново
                raise BanDetected(
                    f"{streak} URL подряд получили только HTTP 403 "
                    f"(последний: {url_at_streak}) — похоже на бан, а не рейт-лимит"
                )
        else:
            self._ban_streak = 0
        return None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()
