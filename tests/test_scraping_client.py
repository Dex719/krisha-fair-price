"""Тесты PoliteClient: семантика 403 (бан) vs 429 (троттлинг) vs 468 (anti-bot челлендж).

403/429 — issue #101; 468 (SafeLine WAF) — спека `.kiro/specs/safeline-468`.
"""

import httpx
import pytest

from krisha.scraping.client import (
    BanDetected,
    ChallengeBlocked,
    PoliteClient,
    SourceUnavailable,
    StickyCookies,
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _client(max_retries=2, ban_streak_threshold=2) -> PoliteClient:
    return PoliteClient(
        delay_range=(0, 0),
        max_retries=max_retries,
        throttle_wait_s=0,
        ban_streak_threshold=ban_streak_threshold,
    )


def test_429_backs_off_and_succeeds_without_touching_ban_streak():
    codes = iter([429, 200])
    client = _client()
    client._client.get = lambda url: _FakeResponse(next(codes), text="ok")

    assert client.get("http://x/1") == "ok"
    assert client._ban_streak == 0


def test_429_alone_never_raises_ban_detected():
    client = _client(max_retries=1, ban_streak_threshold=1)
    client._client.get = lambda url: _FakeResponse(429)

    assert client.get("http://x/1") is None
    assert client._ban_streak == 0


def test_403_streak_raises_ban_detected_after_threshold():
    client = _client(max_retries=1, ban_streak_threshold=2)
    client._client.get = lambda url: _FakeResponse(403)

    assert client.get("http://x/1") is None  # 1-й URL: streak=1, ниже порога
    with pytest.raises(BanDetected):
        client.get("http://x/2")  # 2-й URL подряд: streak=2 >= порога


def test_403_streak_resets_on_success():
    responses = iter([_FakeResponse(403), _FakeResponse(200, text="ok"), _FakeResponse(403)])
    client = _client(max_retries=1, ban_streak_threshold=2)
    client._client.get = lambda url: next(responses)

    assert client.get("http://x/1") is None  # 403 → streak=1
    assert client.get("http://x/2") == "ok"  # успех → streak сброшен
    assert client.get("http://x/3") is None  # 403 → streak=1 снова, ниже порога


def test_mixed_403_and_429_on_one_url_does_not_count_toward_streak():
    """Один URL, где часть попыток 403 и часть 429 — не «все попытки 403»,
    не должен продвигать серию бана (это скорее нестабильный троттлинг)."""
    codes = iter([403, 429])
    client = _client(max_retries=2, ban_streak_threshold=1)
    client._client.get = lambda url: _FakeResponse(next(codes))

    assert client.get("http://x/1") is None
    assert client._ban_streak == 0


def test_network_error_resets_streak():
    import httpx

    def raise_error(url):
        raise httpx.ConnectError("boom")

    client = _client(max_retries=1, ban_streak_threshold=1)
    client._client.get = raise_error

    assert client.get("http://x/1") is None
    assert client._ban_streak == 0


# --- SafeLine WAF: HTTP 468 + страница proof-of-work -----------------------
# Ключевое отличие от 403/429: отказ привязан к СЕССИИ (cookie `sl-session`),
# поэтому ретрай обязан идти со свежим клиентом. Мокаем именно фабрику сессий,
# а не `_client.get`, — иначе после сброса подмена бы слетела.

CHALLENGE_BODY = (
    '<!DOCTYPE html><html><head><meta charset="utf-8">'
    '<link rel="icon" href="/.safeline/static/favicon.png" type="image/png">'
    '<title id="slg-title"></title></head><body>_pow</body></html>'
)


class _FakeSession:
    """Сессия, отдающая коды из общего итератора; помнит, закрывали ли её."""

    def __init__(self, codes, body_for):
        self.codes = codes
        self.body_for = body_for
        self.closed = False
        self.calls = 0

    def get(self, url):
        self.calls += 1
        code = next(self.codes)
        if isinstance(code, Exception):  # сетевой сбой вместо ответа
            raise code
        return _FakeResponse(code, text=self.body_for(code))

    def close(self):
        self.closed = True


def _challenge_client(codes, *, max_retries=3, body_for=None):
    """PoliteClient, у которого каждая новая сессия — свежий _FakeSession."""
    body_for = body_for or (lambda code: CHALLENGE_BODY if code == 468 else "ok")
    it = iter(codes)
    client = PoliteClient(
        delay_range=(0, 0), max_retries=max_retries, throttle_wait_s=0,
        raise_on_challenge=True,
    )
    sessions = []

    def factory():
        session = _FakeSession(it, body_for)
        sessions.append(session)
        return session

    client._new_session = factory
    client._client = factory()
    client.challenge_wait_s = 0
    return client, sessions


def test_468_retries_with_a_fresh_session_and_succeeds():
    """AC-1.1: два челленджа, потом 200 — клиент отдаёт HTML, счётчик считает 468."""
    client, sessions = _challenge_client([468, 468, 200])

    assert client.get("http://x/1") == "ok"
    assert client.counters["http_468"] == 2
    assert client.counters["http_200"] == 1


def test_468_creates_a_new_session_each_attempt():
    """AC-1.3: после челленджа внутренний клиент — ДРУГОЙ объект, старый закрыт."""
    client, sessions = _challenge_client([468, 468, 200])
    first = client._client

    client.get("http://x/1")

    assert len(sessions) == 3, "по сессии на каждую попытку"
    assert client._client is not first
    assert first.closed is True


def test_468_on_every_attempt_raises_challenge_blocked():
    """AC-1.2: исчерпали попытки — это внешний отказ, а не None."""
    client, _ = _challenge_client([468, 468, 468])

    with pytest.raises(ChallengeBlocked):
        client.get("http://x/1")


def test_challenge_page_served_with_http_200_is_not_parsed_as_listing():
    """AC-3.1: челлендж под кодом 200 распознаётся по разметке, а не уезжает в парсер."""
    client, _ = _challenge_client(
        [200, 200], max_retries=2, body_for=lambda code: CHALLENGE_BODY
    )

    with pytest.raises(ChallengeBlocked):
        client.get("http://x/1")
    assert client.counters["http_468"] == 2
    assert client.counters["http_200"] == 0


def test_challenge_does_not_count_toward_ban_streak():
    """Челлендж — не бан по IP: серию 403 он не продолжает и не сбрасывает в бан."""
    client, _ = _challenge_client([468, 468, 468], max_retries=3)
    client.ban_streak_threshold = 1

    with pytest.raises(ChallengeBlocked):
        client.get("http://x/1")
    assert client._ban_streak == 0


def test_mixed_challenge_and_404_returns_none_without_raising():
    """404 — не отказ, а ответ источника «объявления нет»: None без исключения,
    даже если перед ним был челлендж."""
    client, _ = _challenge_client([468, 404], max_retries=2)

    assert client.get("http://x/1") is None
    assert client.counters["http_468"] == 1
    assert client.counters["http_404"] == 1


def test_crawler_default_returns_none_instead_of_raising():
    """Краулер ловит только BanDetected: ChallengeBlocked ему прилетать не должен.

    Ночной проход разбирает неуспех сам (непокрытый шард, пропущенный лот) —
    новое исключение по умолчанию уронило бы его трейсбеком на ровном месте.
    Смена сессии при этом работает для всех: она и есть польза для rescrape,
    где за один проход насчитали 3648 челленджей.
    """
    client, sessions = _challenge_client([468, 468, 468])
    client.raise_on_challenge = False

    assert client.get("http://x/1") is None
    assert client.counters["http_468"] == 3
    assert len(sessions) == 4, "сессия меняется и без подъёма исключения"


# --- Переоткрыто 2026-09-23 (safeline-468 §7): на проде 502 остался ----------
# Правило «исключение, только если ВСЕ попытки 468» отправляло в 502 любую
# смесь: челлендж + таймаут, 403-блок SafeLine, 403 krisha. На проде фикс не
# сработал ни разу — 4 × 502 из 14 смоуков после выката.


def test_mixed_challenge_and_timeout_is_challenge_blocked():
    """AC-9.1: «468, таймаут, 468» — это отказ источника (503), а не 502."""
    client, _ = _challenge_client([468, httpx.ReadTimeout("slow"), 468])

    with pytest.raises(ChallengeBlocked):
        client.get("http://x/1")
    assert client.counters["http_468"] == 2
    assert client.counters["errors"] == 1


def test_timeouts_only_are_source_unavailable_not_challenge():
    client, _ = _challenge_client([httpx.ConnectTimeout("c")] * 3)

    with pytest.raises(SourceUnavailable) as err:
        client.get("http://x/1")
    assert not isinstance(err.value, ChallengeBlocked)


def test_plain_403_is_source_unavailable_for_user_but_none_for_crawler():
    """AC-9.2: 403 без страницы SafeLine — пользователю 503-класс, краулеру —
    прежний None (серия банов у краулера — отдельная, проверена выше)."""
    body = lambda code: "Forbidden"  # noqa: E731
    user, _ = _challenge_client([403, 403, 403], body_for=body)
    with pytest.raises(SourceUnavailable) as err:
        user.get("http://x/1")
    assert not isinstance(err.value, ChallengeBlocked)

    crawler, _ = _challenge_client([403, 403, 403], body_for=body)
    crawler.raise_on_challenge = False
    crawler.ban_streak_threshold = 5
    assert crawler.get("http://x/1") is None


def test_safeline_403_block_counts_waf_block_and_keeps_ban_semantics():
    """AC-8.1: 403 со страницей SafeLine — блок WAF. Для краулера это всё ещё
    403: серия ведёт к BanDetected (issue #101); сессия при этом сброшена."""
    client, sessions = _challenge_client([403], max_retries=1, body_for=lambda code: CHALLENGE_BODY)
    client.raise_on_challenge = False
    client.ban_streak_threshold = 1

    with pytest.raises(BanDetected):
        client.get("http://x/1")
    assert client.counters["http_403"] == 1
    assert client.stats["waf_block"] == 1
    assert client.counters["http_468"] == 0
    assert len(sessions) == 2, "помеченную WAF сессию дальше не передаём"


def test_safeline_403_block_on_user_path_is_challenge_blocked():
    client, _ = _challenge_client([403, 403, 403], body_for=lambda code: CHALLENGE_BODY)

    with pytest.raises(ChallengeBlocked):
        client.get("http://x/1")
    assert client.stats["waf_block"] == 3
    # waf_block — подвид 403, а не отдельный запрос: rescrape считает число
    # запросов прохода суммой counters (fit_detail_caps) — задваивать нельзя.
    assert sum(client.counters.values()) == 3


def test_challenge_page_under_any_non_403_status_is_a_challenge():
    """Страница SafeLine под 503/202/… — тоже челлендж, а не «прочий код»."""
    client, _ = _challenge_client(
        [503, 200], body_for=lambda code: CHALLENGE_BODY if code == 503 else "ok"
    )

    assert client.get("http://x/1") == "ok"
    assert client.counters["http_468"] == 1
    assert client.counters["http_other"] == 0


def test_on_attempt_hook_sees_every_outcome_and_cannot_break_the_scrape():
    """FR-10: исходы попыток уходят наружу (в /api/metrics); упавший хук —
    не повод ронять скрейп."""
    seen = []
    client, _ = _challenge_client([468, httpx.ReadTimeout("t"), 200])
    client.on_attempt = seen.append
    assert client.get("http://x/1") == "ok"
    assert seen == ["http_468", "errors", "http_200"]

    broken, _ = _challenge_client([200])

    def explode(name):
        raise RuntimeError("метрики легли")

    broken.on_attempt = explode
    assert broken.get("http://x/2") == "ok"


# --- Липкие куки (AC-7.1, AC-7.2): настоящий httpx, подставной транспорт ------


def _krisha(monkeypatch, handler):
    """Все httpx.Client в тесте ходят в handler вместо сети."""
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))


def _user_client(store):
    return PoliteClient(
        delay_range=(0, 0), max_retries=3, throttle_wait_s=0, challenge_wait_s=0,
        raise_on_challenge=True, cookie_store=store,
    )


def test_sticky_cookies_seed_the_next_user_client(monkeypatch):
    """AC-7.1: сессия, дошедшая до объявления (ведро A), засевает следующую."""
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        return httpx.Response(200, text="ok", headers={"set-cookie": "kraid=A1; Path=/"})

    _krisha(monkeypatch, handler)
    store = StickyCookies()

    with _user_client(store) as first:
        assert first.get("https://krisha.kz/a/show/1") == "ok"
    with _user_client(store) as second:
        assert second.get("https://krisha.kz/a/show/2") == "ok"

    assert seen[0] is None, "первый клиент процесса стартует без кук"
    assert seen[1] == "kraid=A1", "второй — с куками удачной сессии"


def test_safeline_page_forgets_sticky_cookies_and_redraws(monkeypatch):
    """AC-7.2: засеянная сессия упёрлась в SafeLine (ведро B) — куки забыты для
    всех, повтор идёт с чистой сессией, удачная — запоминается заново."""
    seen = []

    def handler(request):
        cookie = request.headers.get("cookie")
        seen.append(cookie)
        if cookie == "kraid=B7":
            return httpx.Response(468, text=CHALLENGE_BODY, headers={"set-cookie": "sl-session=x; Path=/"})
        return httpx.Response(200, text="ok", headers={"set-cookie": "kraid=A2; Path=/"})

    _krisha(monkeypatch, handler)
    store = StickyCookies()
    store.remember(httpx.Cookies({"kraid": "B7"}))

    with _user_client(store) as client:
        assert client.get("https://krisha.kz/a/show/3") == "ok"

    assert seen == ["kraid=B7", None]
    assert [c.value for c in store.cookies().jar] == ["A2"]
