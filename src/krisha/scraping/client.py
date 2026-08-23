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
        timeout: float | httpx.Timeout | None = None,
    ):
        self.delay_range = delay_range
        self.max_retries = max(1, int(max_retries))
        self.throttle_wait_s = throttle_wait_s
        self.ban_streak_threshold = max(1, int(ban_streak_threshold))
        self._ban_streak = 0
        # issue #152: без телеметрии «подходим ли мы к грани» ненаблюдаемо —
        # единичные 403 и 429 тонут в warning-логах, а latency растёт задолго
        # до первого бана. Счётчики уезжают в summary-JSON прохода.
        self.counters: dict[str, int] = {
            "http_200": 0, "http_403": 0, "http_404": 0,
            "http_429": 0, "http_other": 0, "errors": 0,
        }
        self._latencies: list[float] = []
        self._throttled_down = False
        # timeout настраивается по контексту: краулеру не жалко ждать 30 с,
        # а пользовательский путь (веб/бот) обязан ответить за секунды. При
        # подвисшем коннекте краулерный бюджет давал worst-case
        # max_retries × REQUEST_TIMEOUT ≈ минуту на ОДИН запрос — и десять
        # таких намертво занимали слоты предикта.
        self.timeout = REQUEST_TIMEOUT if timeout is None else timeout
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"},
            timeout=self.timeout,
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
            started = time.monotonic()
            try:
                resp = self._client.get(url)
                self._latencies.append((time.monotonic() - started) * 1000)
                if resp.status_code == 200:
                    self.counters["http_200"] += 1
                    self._ban_streak = 0
                    return resp.text
                if resp.status_code == 404:
                    self.counters["http_404"] += 1
                    self._ban_streak = 0
                    logger.warning("404: %s", url)
                    return None
                if resp.status_code == 429:
                    # Троттлят, не банят — эскалирующий бэкофф оправдан.
                    self.counters["http_429"] += 1
                    self._slow_down_if_throttled()
                    all_403 = False
                    wait = self.throttle_wait_s * attempt
                    logger.warning("HTTP 429 (троттлинг) на %s, ждём %s сек", url, wait)
                    if attempt < self.max_retries:
                        time.sleep(wait)
                    continue
                if resp.status_code == 403:
                    self.counters["http_403"] += 1
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
                self.counters["http_other"] += 1
                logger.warning("HTTP %s: %s", resp.status_code, url)
            except httpx.HTTPError as exc:
                all_403 = False
                self.counters["errors"] += 1
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

    THROTTLE_ESCALATE_AFTER = 3   # столько 429 за проход — и замедляемся
    THROTTLE_SLOWDOWN_FACTOR = 1.5

    def _slow_down_if_throttled(self) -> None:
        """Разовое замедление, если сервер начал троттлить (issue #152).

        Дешевле сбавить темп самому, чем поймать бан: 429 — это прямое
        «притормози», и продолжать в том же ритме до серии 403 неразумно.
        Замедляемся один раз за проход, чтобы паузы не уползли в бесконечность.
        """
        if self._throttled_down or self.counters["http_429"] < self.THROTTLE_ESCALATE_AFTER:
            return
        lo, hi = self.delay_range
        self.delay_range = (lo * self.THROTTLE_SLOWDOWN_FACTOR, hi * self.THROTTLE_SLOWDOWN_FACTOR)
        self._throttled_down = True
        logger.warning(
            "Получили %s×429 — снижаю темп до пауз %.1f–%.1f с",
            self.counters["http_429"], *self.delay_range,
        )

    @property
    def stats(self) -> dict:
        """Сводка по проходу для summary-JSON."""
        lat = sorted(self._latencies)
        def pct(p: float) -> int:
            if not lat:
                return 0
            return int(lat[min(len(lat) - 1, int(len(lat) * p))])
        return {
            **self.counters,
            "latency_p50_ms": pct(0.5),
            "latency_p90_ms": pct(0.9),
            "throttled_down": self._throttled_down,
        }

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()
