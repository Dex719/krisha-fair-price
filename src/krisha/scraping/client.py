"""Вежливый HTTP-клиент: паузы 2–4 сек, настраиваемые ретраи, единый User-Agent."""

import logging
import random
import threading
import time
from collections.abc import Callable

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


class SourceUnavailable(RuntimeError):
    """Источник не отдал страницу ни на одной попытке — внешний отказ, не наш сбой.

    Пользовательский путь (веб, бот) отвечает на него 503 «источник временно
    не отдаёт объявление»: таймауты, 5xx, троттлинг и блоки krisha — состояние
    чужого сервера. 502 остаётся тому, что сломали мы (не распарсили, модель).
    До 2026-09-23 сюда не попадало ничего, кроме «все попытки — 468»: смесь
    челленджа с таймаутом или 403-блок SafeLine уходили в None → 502, и на
    проде фикс челленджа не сработал ни разу (.kiro/specs/safeline-468, §7).
    """


class ChallengeBlocked(SourceUnavailable):
    """Страница закрыта anti-bot челленджем, а не отдана (SafeLine WAF).

    krisha.kz отвечает HTTP 468 и страницей JS proof-of-work (`/.safeline/`,
    cookie `sl-session`) вместо объявления. Это НЕ бан по IP (403) и не
    троттлинг (429): челлендж выдаётся вероятностно, и следующий запрос с
    ЧИСТОЙ сессией обычно проходит.

    Отдельный класс нужен, чтобы вызывающий отличил «внешний источник
    временно не пускает» (503 + Retry-After) от «мы не смогли разобрать
    объявление» (502). Раньше 468 попадал в ветку «прочее»: без паузы, без
    счётчика, и ретрай шёл тем же клиентом — то есть с тем же `sl-session`,
    который WAF уже отклонил, и потому был бесполезен по построению.

    Поднимается, если хоть одна попытка упёрлась в страницу SafeLine —
    челлендж или 403-блок, — даже когда остальные кончились таймаутом.
    """


# 468 — нестандартный код SafeLine. Держим множеством: соседние WAF-коды
# добавляются сюда, а не новой веткой в `get`.
CHALLENGE_STATUSES = frozenset({468})
# Тот же челлендж иногда приезжает с кодом 200 — тогда его отличает только
# разметка. Маркеры лежат в первых сотнях байт <head>, поэтому смотрим голову
# ответа, а не всю страницу на 227 КБ.
_CHALLENGE_MARKERS = ("/.safeline/", 'id="slg-title"')
_CHALLENGE_SNIFF_BYTES = 4096


def looks_like_challenge(text: str) -> bool:
    """Похоже ли тело ответа на страницу SafeLine (челлендж или блок)."""
    head = text[:_CHALLENGE_SNIFF_BYTES]
    return any(marker in head for marker in _CHALLENGE_MARKERS)


class StickyCookies:
    """Куки последней удачной пользовательской сессии — общие на процесс.

    krisha делит посетителей на вёдра (`x-kls-bucket`): A отдаёт объявление,
    B — челлендж SafeLine, и ведро закрепляет кука `kraid` (замер 2026-09-23:
    12/12 запросов сессии из A остались в A, 4/4 с `kraid` из B — в B).
    Краулер держит один клиент и после первого сброса оседает в A; а
    пользовательский путь открывал новую сессию на КАЖДЫЙ запрос и каждый раз
    заново тянул жребий — на 2026-09-23 75% в пользу B. Храним куки, которые
    сайт выдал сам, как это делает браузер, и засеваем ими следующий клиент;
    забываем, как только сессия упёрлась в SafeLine (.kiro/specs/safeline-468, §7).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[tuple[str, str, str, str]] = []  # name, value, domain, path

    def cookies(self) -> httpx.Cookies:
        """Копия для нового клиента — клиенты не делят один изменяемый jar."""
        jar = httpx.Cookies()
        with self._lock:
            for name, value, domain, path in self._items:
                jar.set(name, value, domain=domain, path=path)
        return jar

    def remember(self, cookies: httpx.Cookies) -> None:
        items = [(c.name, c.value or "", c.domain, c.path) for c in cookies.jar]
        with self._lock:
            self._items = items

    def forget(self) -> None:
        with self._lock:
            self._items = []

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


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
        challenge_wait_s: float = 1.0,
        raise_on_challenge: bool = False,
        cookie_store: StickyCookies | None = None,
        on_attempt: Callable[[str], None] | None = None,
    ):
        self.delay_range = delay_range
        self.max_retries = max(1, int(max_retries))
        self.throttle_wait_s = throttle_wait_s
        # Челлендж лечится не ожиданием, а СМЕНОЙ сессии, поэтому пауза здесь
        # короткая: длинный бэкофф (как у 429) только жёг бы бюджет прохода и
        # терпение человека, глядящего на спиннер.
        self.challenge_wait_s = challenge_wait_s
        # По умолчанию False — ровно прежнее поведение: исчерпали попытки,
        # вернули None. Краулер и sweep разбирают неуспех сами (непокрытый
        # шард, пропущенный лот) и ловят только BanDetected; прилети им
        # ChallengeBlocked — ночной проход лёг бы трейсбеком на ровном месте.
        # Пользовательскому пути (веб, бот) различать причину НУЖНО: от неё
        # зависит, 503 это или 502, — там флаг включают явно. С флагом любой
        # исход «страницы нет, и это не 404» поднимает SourceUnavailable.
        self.raise_on_challenge = raise_on_challenge
        # Липкие куки пользовательского пути (см. StickyCookies). Краулеру не
        # нужны: его единственный клиент и так держит свою сессию весь проход.
        self.cookie_store = cookie_store
        # Хук на каждую попытку с именем счётчика ("http_200", "waf_block", …):
        # веб суммирует исходы в /api/metrics — причина отказа видна без логов.
        self.on_attempt = on_attempt
        self.ban_streak_threshold = max(1, int(ban_streak_threshold))
        self._ban_streak = 0
        # issue #152: без телеметрии «подходим ли мы к грани» ненаблюдаемо —
        # единичные 403 и 429 тонут в warning-логах, а latency растёт задолго
        # до первого бана. Счётчики уезжают в summary-JSON прохода.
        self.counters: dict[str, int] = {
            "http_200": 0, "http_403": 0, "http_404": 0,
            "http_429": 0, "http_468": 0, "http_other": 0, "errors": 0,
        }
        # Подвид http_403 (403 со страницей SafeLine), а не отдельный исход:
        # сумма counters обязана равняться числу запросов — по ней rescrape
        # планирует бюджет докачки (fit_detail_caps). Поэтому отдельным полем.
        self.waf_blocks = 0
        self._latencies: list[float] = []
        self._throttled_down = False
        # timeout настраивается по контексту: краулеру не жалко ждать 30 с,
        # а пользовательский путь (веб/бот) обязан ответить за секунды. При
        # подвисшем коннекте краулерный бюджет давал worst-case
        # max_retries × REQUEST_TIMEOUT ≈ минуту на ОДИН запрос — и десять
        # таких намертво занимали слоты предикта.
        self.timeout = REQUEST_TIMEOUT if timeout is None else timeout
        self._client = self._new_session(seed=True)

    def _new_session(self, seed: bool = False) -> httpx.Client:
        """Новый HTTP-клиент со свежими соединениями.

        seed=True — куки из хранилища (первая сессия пользовательского
        клиента); иначе чистый cookie jar: после SafeLine нужен новый жребий.
        """
        cookies = self.cookie_store.cookies() if seed and self.cookie_store is not None else None
        return httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"},
            timeout=self.timeout,
            follow_redirects=True,
            cookies=cookies,
        )

    def _reset_session(self) -> None:
        """Выбросить текущую сессию и начать новую.

        Ключ к обходу SafeLine: сессия, единожды получившая челлендж, ЗАЛИПАЕТ —
        замер на 8 объявлениях показал 0 успехов из 4 ретраев в той же сессии
        против 8 из 8 со свежей (закрепляет её не sl-session, а кука `kraid`,
        см. StickyCookies). Пока отказ зависел только от темпа (429) или IP
        (403), переиспользование клиента было правильным; отказ, привязанный к
        сессии, требует её снести.
        """
        old, self._client = self._client, self._new_session()
        try:
            old.close()
        except Exception:  # noqa: BLE001 — закрытие старой сессии не должно ломать проход
            logger.debug("не удалось закрыть старую сессию", exc_info=True)

    def _drop_flagged_session(self) -> None:
        """Сессию пометил SafeLine: забыть её куки для всех и начать заново.

        Уточнение 2026-09-23: залипает не sl-session, а ведро, которое
        закрепляет кука `kraid` (см. StickyCookies), — отсюда сброс ВСЕХ кук.
        """
        if self.cookie_store is not None:
            self.cookie_store.forget()
        self._reset_session()

    def _count(self, name: str) -> None:
        """Исход попытки: ровно один счётчик на запрос + событие наружу."""
        self.counters[name] += 1
        self._emit(name)

    def _emit(self, name: str) -> None:
        if self.on_attempt is not None:
            try:
                self.on_attempt(name)
            except Exception:  # noqa: BLE001 — телеметрия не должна ломать скрейп
                logger.debug("on_attempt: хук упал", exc_info=True)

    def get(self, url: str) -> str | None:
        """GET с паузой и ретраями. Возвращает HTML или None при неудаче.

        Поднимает `BanDetected`, если этот URL и предыдущие подряд (см.
        `ban_streak_threshold`) отдали 403 на КАЖДОЙ попытке — см. класс.
        Любой другой исход (успех, 404, 429, сетевая ошибка) сбрасывает
        счётчик серии.

        При anti-bot челлендже каждая следующая попытка идёт со СВЕЖЕЙ
        сессией (см. `_reset_session`) — это единственное, что его лечит.
        Если не прошла ни одна попытка, поведение зависит от
        `raise_on_challenge`: по умолчанию возвращается None (как при любом
        другом неуспехе — так краулер и разбирал это годами), а с флагом
        поднимается `SourceUnavailable` (`ChallengeBlocked`, если хоть одна
        попытка упёрлась в SafeLine), чтобы пользовательский путь отличил
        «источник не отдал» от «не смогли разобрать». 404 — не отказ, а ответ
        источника «объявления нет»: None без исключения при любом флаге.
        """
        saw_403 = False
        all_403 = True
        attempts_done = 0
        waf_hits = 0  # попытки, упёршиеся в страницу SafeLine (челлендж или блок)
        for attempt in range(1, self.max_retries + 1):
            time.sleep(random.uniform(*self.delay_range))
            started = time.monotonic()
            attempts_done += 1
            try:
                resp = self._client.get(url)
                self._latencies.append((time.monotonic() - started) * 1000)
                safeline = looks_like_challenge(resp.text)
                # Челлендж проверяем ПЕРВЫМ: SafeLine отдаёт свою страницу и
                # под кодом 468, и иногда под 200 — во втором случае она молча
                # уехала бы в парсер и вернулась «не удалось распарсить». По
                # разметке узнаём при ЛЮБОМ коде, кроме 403: 403 со страницей
                # SafeLine — блок, он разобран в ветке 403 ниже.
                if resp.status_code in CHALLENGE_STATUSES or (safeline and resp.status_code != 403):
                    self._count("http_468")
                    waf_hits += 1
                    # Челлендж — не бан по IP: серию 403 он не продолжает.
                    all_403 = False
                    self._ban_streak = 0
                    logger.warning(
                        "HTTP %s: anti-bot челлендж на %s (попытка %s) — меняем сессию",
                        resp.status_code, url, attempt,
                    )
                    self._drop_flagged_session()
                    if attempt < self.max_retries:
                        time.sleep(self.challenge_wait_s)
                    continue
                if resp.status_code == 200:
                    self._count("http_200")
                    self._ban_streak = 0
                    if self.cookie_store is not None:
                        # Сессия дошла до объявления — её куки (ведро A) отдаём
                        # следующим пользовательским запросам.
                        self.cookie_store.remember(self._client.cookies)
                    return resp.text
                if resp.status_code == 404:
                    self._count("http_404")
                    self._ban_streak = 0
                    logger.warning("404: %s", url)
                    return None
                if resp.status_code == 429:
                    # Троттлят, не банят — эскалирующий бэкофф оправдан.
                    self._count("http_429")
                    self._slow_down_if_throttled()
                    all_403 = False
                    wait = self.throttle_wait_s * attempt
                    logger.warning("HTTP 429 (троттлинг) на %s, ждём %s сек", url, wait)
                    if attempt < self.max_retries:
                        time.sleep(wait)
                    continue
                if resp.status_code == 403:
                    self._count("http_403")
                    saw_403 = True
                    if safeline:
                        # Блок SafeLine (403 со своей страницей). Для краулера это
                        # по-прежнему 403 — серия ведёт к BanDetected (issue #101),
                        # — но помеченную WAF сессию дальше не передаём.
                        self.waf_blocks += 1
                        self._emit("waf_block")
                        waf_hits += 1
                        self._drop_flagged_session()
                    logger.warning(
                        "HTTP 403%s на %s (попытка %s) — тем же UA/IP; если это бан, "
                        "ретрай не поможет",
                        " (блок SafeLine)" if safeline else "",
                        url,
                        attempt,
                    )
                    if attempt < self.max_retries:
                        time.sleep(self.throttle_wait_s)
                    continue
                all_403 = False
                self._count("http_other")
                logger.warning("HTTP %s: %s", resp.status_code, url)
            except httpx.HTTPError as exc:
                all_403 = False
                self._count("errors")
                logger.warning("Ошибка запроса %s (попытка %s): %s", url, attempt, exc)

        # Страницы нет ни на одной попытке, и это не 404 — внешний отказ, а не
        # наша неспособность разобрать объявление. Тем, кто умеет различать
        # (пользовательский путь: 503 против 502), говорим об этом явно — при
        # ЛЮБОЙ смеси исходов: раньше исключение было только на «три из трёх
        # 468», а смесь с таймаутом или 403-блок уходили в 502.
        if self.raise_on_challenge:
            if waf_hits:
                raise ChallengeBlocked(
                    f"{attempts_done} попыток без страницы, из них {waf_hits} — SafeLine "
                    f"({url}) — источник временно не отдаёт страницу"
                )
            raise SourceUnavailable(
                f"{attempts_done} попыток без страницы ({url}) — источник временно недоступен"
            )

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
            "waf_block": self.waf_blocks,
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
